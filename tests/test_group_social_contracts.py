# -*- coding: utf-8 -*-
"""人格迭代契约测试：氛围感知 / 扮演强度 / 名场面 / 接梗边界。

纯函数断言为主（不依赖 astrbot 运行时）；挂载方法用 GroupObservationMixin
的轻量 harness 直接调用，全部走确定性时间戳。
"""
from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_private_companion.domains.social.group_mood import (
    MOOD_LABELS,
    project_group_mood,
    project_group_mood_prompt_facts,
    settle_group_mood,
)
from astrbot_plugin_private_companion.domains.social.roleplay_strength import (
    project_roleplay_strength,
)
from astrbot_plugin_private_companion.domains.social.group_moments import (
    extract_group_moment_candidates,
    extract_moment_portrait_candidates,
    select_group_moments_for_prompt,
    settle_group_moments,
)
from astrbot_plugin_private_companion.domains.social.joke_boundary import (
    correct_mood_for_member,
    is_serious_objection,
    joke_guard_suggestion,
    settle_joke_boundary,
)
from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    render_prompt_sections,
)

T0 = 1_000_000.0


def _mood_snapshot(*, tease: float = 0.0, tension: float = 0.0, updated_at: float = T0) -> dict:
    scores = {label: 0.0 for label in MOOD_LABELS}
    scores["tease"] = tease
    top = "tease" if tease > 0 else "dead_silence"
    return {
        "version": "group_mood.v1",
        "mood_scores": scores,
        "top_mood": top,
        "social_tension": tension,
        "updated_at": updated_at,
        "decayed_at": updated_at,
        "message_count": 1,
    }


class GroupSocialHarness(GroupObservationMixin):
    """组合 GroupObservationMixin 的零依赖 harness。"""

    def __init__(self) -> None:
        self._settings: dict[str, object] = {
            "enable_group_mood_detection": False,
            "enable_group_roleplay_strength": False,
            "enable_group_moments": False,
            "enable_group_joke_guard": False,
            "enable_group_moment_portrait": False,
        }
        self.messages: list[dict] = []
        self.groups: dict[str, dict] = {}
        self.saved_sections: list[object] = []
        self.data: dict[str, object] = {"users": {}}

    def persona_setting(self, key: str, default=None):
        return self._settings.get(key, default)

    def _filtered_group_recent_messages(self, group: dict) -> list[dict]:
        return list(self.messages)

    def _save_data_sync(self, sections=None) -> bool:
        self.saved_sections.append(sections)
        return True

    def _get_group(self, group_id: str) -> dict:
        return self.groups.setdefault(group_id, {})


class GroupMoodContractTests(unittest.TestCase):
    def test_empty_to_tease(self):
        mood = settle_group_mood(None, messages=[{"text": "哈哈笑死，没绷住"}], now=T0)
        self.assertEqual("group_mood.v1", mood["version"])
        self.assertEqual("tease", mood["top_mood"])
        self.assertGreater(mood["mood_scores"]["tease"], 0)
        self.assertGreater(mood["social_tension"], 0)

    def test_mood_decays_over_time(self):
        settled = settle_group_mood(None, messages=[{"text": "哈哈笑死"}], now=T0)
        later = project_group_mood(settled, now=T0 + 999_999)
        self.assertLess(later["mood_scores"]["tease"], settled["mood_scores"]["tease"])
        self.assertIn(later["top_mood"], MOOD_LABELS)

    def test_reprocessing_same_window_does_not_accumulate(self):
        message = {"message_id": "m1", "sender_id": "u1", "text": "哈哈笑死", "ts": T0}
        first = settle_group_mood(None, messages=[message], now=T0)
        second = settle_group_mood(first, messages=[message], now=T0 + 1)
        self.assertEqual(first["mood_scores"], second["mood_scores"])
        self.assertEqual(first["message_count"], second["message_count"])

    def test_blank_window_raises_dead_silence(self):
        mood = settle_group_mood(None, messages=[], now=T0)
        self.assertGreater(mood["mood_scores"]["dead_silence"], 0)

    def test_prompt_projection_exposes_facts_without_injection_prose(self):
        facts = project_group_mood_prompt_facts(
            _mood_snapshot(tease=70),
            now=T0,
        )

        self.assertEqual("tease", facts["top_mood"])
        self.assertEqual("调侃", facts["top_mood_label"])
        self.assertEqual("low", facts["tension_level"])
        section = GroupSocialHarness._group_social_mood_prompt_section(
            _mood_snapshot(tease=70),
            now=T0,
        )
        self.assertEqual(
            "当前氛围以「调侃」为主；群内气氛轻松无火药味",
            section.content,
        )


class RoleplayStrengthContractTests(unittest.TestCase):
    def test_tease_maps_to_high_exaggerate(self):
        projection = project_roleplay_strength(_mood_snapshot(tease=70), now=T0)
        self.assertGreaterEqual(projection["exaggerate"], 60)
        self.assertEqual("playful_high", projection["strength_band"])
        self.assertNotIn("voice", projection)
        self.assertNotIn("band_voice", projection)
        section = GroupSocialHarness._group_roleplay_strength_prompt_section(
            _mood_snapshot(tease=70),
            now=T0,
        )
        self.assertEqual(
            "群聊正处在玩闹气氛，可以适度夸张、接梗、起哄，让回复更有参与感；不要刻意抢话或攻击谁",
            section.content,
        )

    def test_serious_mood_stays_reserved(self):
        scores = {label: 0.0 for label in MOOD_LABELS}
        scores["serious"] = 80.0
        mood = {
            "version": "group_mood.v1",
            "mood_scores": scores,
            "top_mood": "serious",
            "social_tension": 10.0,
            "updated_at": T0,
            "decayed_at": T0,
            "message_count": 1,
        }
        projection = project_roleplay_strength(mood, now=T0)
        self.assertLessEqual(projection["exaggerate"], 40)

    def test_tension_dampens_exaggerate(self):
        calm = project_roleplay_strength(_mood_snapshot(tease=70, tension=0), now=T0)
        tense = project_roleplay_strength(_mood_snapshot(tease=70, tension=80), now=T0)
        self.assertLess(tense["exaggerate"], calm["exaggerate"])

    def test_cold_band_modifier_caps_playfulness(self):
        projection = project_roleplay_strength(
            _mood_snapshot(tease=60),
            expression_band="hurt",
            now=T0,
        )
        self.assertLess(projection["exaggerate"], 30)


class JokeBoundaryContractTests(unittest.TestCase):
    def test_serious_objection_accumulates_to_block(self):
        messages = [{"sender_id": "u1", "text": "别拿我开玩笑"}] * 3
        boundary = settle_joke_boundary(None, messages=messages, now=T0)
        member = boundary["members"]["u1"]
        self.assertGreaterEqual(member["sensitivity"], 60)
        self.assertEqual(3, member["objection_count"])
        suggestion = joke_guard_suggestion(boundary, member_id="u1")
        self.assertTrue(suggestion["blocked"])
        self.assertEqual(
            "repeated_serious_objection_or_recall",
            suggestion["reason_code"],
        )
        self.assertNotIn("reason", suggestion)

    def test_recall_signal_raises_recall_count(self):
        boundary = settle_joke_boundary(
            None,
            messages=[{"sender_id": "u2", "kind": "recall"}],
            now=T0,
        )
        self.assertEqual(1, boundary["members"]["u2"]["recall_count"])
        self.assertGreaterEqual(boundary["members"]["u2"]["sensitivity"], 12)

    def test_reprocessing_same_window_does_not_repeat_objection(self):
        message = {"message_id": "m1", "sender_id": "u1", "text": "别拿我开玩笑", "ts": T0}
        first = settle_joke_boundary(None, messages=[message], now=T0)
        second = settle_joke_boundary(first, messages=[message], now=T0 + 1)
        self.assertEqual(first["members"], second["members"])

    def test_objection_detector(self):
        self.assertTrue(is_serious_objection("别开这种玩笑"))
        self.assertFalse(is_serious_objection("哈哈哈哈"))

    def test_correct_mood_dampens_tease_for_blocked_member(self):
        boundary = {
            "version": "joke_boundary.v1",
            "members": {"u1": {"sensitivity": 80.0, "objection_count": 3, "recall_count": 0, "updated_at": T0}},
            "updated_at": T0,
        }
        corrected = correct_mood_for_member(
            _mood_snapshot(tease=70),
            boundary,
            member_id="u1",
            now=T0,
        )
        self.assertEqual("blocked", corrected["joke_guard"])
        self.assertLess(corrected["mood_scores"]["tease"], 20)


class GroupMomentsContractTests(unittest.TestCase):
    def test_spark_reply_candidate_scores(self):
        messages = [
            {"sender_id": "a", "text": "我要看到血流成河", "ts": T0},
            {"sender_id": "b", "text": "笑死 @a 你别说了", "ts": T0 + 1, "reply_to_id": 7},
        ]
        candidates = extract_group_moment_candidates(messages, now=T0 + 2)
        self.assertGreaterEqual(len(candidates), 1)
        spark = candidates[0]
        self.assertGreaterEqual(spark["score"], 3)
        self.assertIn("spark", spark["reasons"])
        self.assertIn("reply", spark["reasons"])

    def test_dedup_on_settle(self):
        candidates = extract_group_moment_candidates(
            [{"sender_id": "a", "text": "经典名场面", "ts": T0}],
            now=T0,
        )
        first = settle_group_moments(None, candidates=candidates, now=T0)
        second = settle_group_moments(first, candidates=candidates, now=T0 + 60)
        self.assertEqual(1, len(second["moments"]))
        self.assertEqual(first["moments"][0]["expires_at"], second["moments"][0]["expires_at"])

    def test_prompt_selection_returns_structured_moment_facts(self):
        candidates = extract_group_moment_candidates(
            [{"sender_id": "漂", "text": "这谁顶得住", "ts": T0}],
            now=T0,
        )
        moments = settle_group_moments(None, candidates=candidates, now=T0)
        selected = select_group_moments_for_prompt(moments, now=T0)
        self.assertEqual("漂", selected[0]["sender"])
        self.assertEqual("这谁顶得住", selected[0]["text"])
        section = GroupSocialHarness._group_social_moments_prompt_section(
            moments,
            now=T0,
        )
        self.assertEqual(
            "漂：这谁顶得住",
            render_prompt_sections(
                [section],
                mode=PromptRenderMode.BODY_ONLY,
            ),
        )


class GroupObservationMountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = GroupSocialHarness()
        self.harness.messages = [
            {"ts": T0 - 3, "sender_id": "a", "name": "A", "text": "哈哈笑死，没绷住"},
            {"ts": T0 - 2, "sender_id": "b", "name": "B", "text": "别拿我开玩笑"},
            {"ts": T0 - 1, "sender_id": "c", "name": "C", "text": "我要看血流成河"},
        ]

    def test_update_group_mood_writes_snapshot(self):
        group: dict = {}
        self.harness._update_group_mood(group, now=T0)
        self.assertEqual("tease", group["social_mood"]["top_mood"])

    def test_update_group_joke_boundary_writes_member(self):
        group: dict = {}
        self.harness._update_group_joke_boundary(group, now=T0)
        self.assertIn("b", group["social_joke_boundary"]["members"])

    def test_update_group_moments_writes_list(self):
        group: dict = {}
        self.harness._update_group_moments(group, now=T0)
        self.assertGreaterEqual(len(group["social_moments"]["moments"]), 1)

    def test_sections_empty_when_disabled(self):
        group: dict = {"social_mood": settle_group_mood(None, messages=[{"text": "哈哈笑死"}], now=T0)}
        sections: list[dict] = []
        self.harness._append_group_social_context_sections(group, sections, sender_id="b")
        self.assertEqual([], sections)

    def test_sections_appear_when_enabled(self):
        group: dict = {
            "social_mood": settle_group_mood(None, messages=[{"text": "哈哈笑死"}], now=T0),
            "social_moments": settle_group_moments(
                None,
                candidates=extract_group_moment_candidates(
                    [{"sender_id": "漂", "text": "这谁顶得住", "ts": T0}],
                    now=T0,
                ),
                now=T0,
            ),
        }
        self.harness._settings.update({
            "enable_group_mood_detection": True,
            "enable_group_roleplay_strength": True,
            "enable_group_moments": True,
            "enable_group_joke_guard": True,
        })
        sections: list[PromptSection] = []
        self.harness._append_group_social_context_sections(group, sections, sender_id="b", now=T0)
        titles = [section.title for section in sections]
        self.assertEqual(
            {
                "group.social_soft_reference",
                "group.social_mood",
                "group.social_moments",
                "group.roleplay_strength",
            },
            {section.key for section in sections},
        )
        self.assertTrue(all(section.source == "group_observation" for section in sections))
        self.assertTrue(any("氛围" in title for title in titles))
        self.assertTrue(any("名场面" in title for title in titles))
        self.assertTrue(any("扮演强度" in title for title in titles))
        rendered = render_prompt_sections(sections)
        self.assertIn('<section title="群聊氛围">', rendered)
        self.assertIn("当前氛围以「调侃」为主", rendered)
        self.assertIn('<section title="群聊名场面（可选回忆）">', rendered)
        self.assertIn("<moments><moment>漂：这谁顶得住</moment></moments>", rendered)
        legacy_body = render_prompt_sections(
            sections,
            mode=PromptRenderMode.BODY_ONLY,
        )
        self.assertIn("漂：这谁顶得住", legacy_body)

    def test_roleplay_uses_current_sender_expression_band(self):
        group: dict = {
            "social_mood": settle_group_mood(
                None, messages=[{"text": "哈哈笑死"}], now=T0
            )
        }
        self.harness.data = {
            "users": {"b": {"current_interaction": {"expression_band": "hurt"}}}
        }
        self.harness._settings["enable_group_roleplay_strength"] = True
        sections: list[PromptSection] = []
        self.harness._append_group_social_context_sections(
            group, sections, sender_id="b", now=T0
        )
        roleplay = next(section for section in sections if section.title == "扮演强度")
        self.assertIn("压低表达强度", roleplay.content)

    def test_joke_reason_code_is_rendered_only_by_section_builder(self):
        boundary = settle_joke_boundary(
            None,
            messages=[{"sender_id": "b", "text": "别拿我开玩笑"}] * 3,
            now=T0,
        )

        section = self.harness._group_joke_boundary_prompt_section(
            boundary,
            member_id="b",
        )

        self.assertIsNotNone(section)
        self.assertEqual("group.joke_boundary", section.key)
        self.assertEqual(
            "该成员已多次严肃反对或撤回玩笑，避免再向其开玩笑",
            section.content,
        )

    def test_async_recall_fills_joke_boundary(self):
        async def run() -> bool:
            return await self.harness._note_group_joke_boundary_recall("group-x", "u9")

        self.harness._settings["enable_group_joke_guard"] = True
        self.assertTrue(asyncio.run(run()))
        boundary = self.harness.groups["group-x"]["social_joke_boundary"]
        self.assertGreaterEqual(boundary["members"]["u9"]["sensitivity"], 12)
        self.assertIn({"groups"}, self.harness.saved_sections)


class MomentPortraitContractTests(unittest.TestCase):
    """Group interaction evidence remains scoped and provisional."""

    def setUp(self) -> None:
        self.harness = GroupSocialHarness()

    def test_extract_moment_portrait_infers_preference_from_spark(self):
        moments = {
            "version": "group_moments.v1",
            "moments": [
                {"hash": "a", "sender": "u1", "text": "哈哈笑死我了", "ts": T0, "expires_at": T0 + 86400, "score": 3.0, "reasons": ["spark"]},
            ],
        }
        candidates = extract_moment_portrait_candidates(moments, now=T0 + 10)
        self.assertEqual(1, len(candidates))
        self.assertEqual("u1", candidates[0]["sender"])
        self.assertEqual("communication_preference", candidates[0]["dimension"])
        self.assertIn("笑死", candidates[0]["claim"])

    def test_extract_moment_portrait_infers_boundary_from_discomfort(self):
        moments = {
            "version": "group_moments.v1",
            "moments": [
                {"hash": "b", "sender": "u2", "text": "别拿我开玩笑", "ts": T0, "expires_at": T0 + 86400, "score": 2.0, "reasons": ["spark"]},
            ],
        }
        candidates = extract_moment_portrait_candidates(moments, now=T0 + 10)
        self.assertEqual(1, len(candidates))
        self.assertEqual("boundary", candidates[0]["dimension"])

    def test_extract_moment_portrait_skips_low_score_noise(self):
        moments = {
            "version": "group_moments.v1",
            "moments": [
                {"hash": "c", "sender": "u3", "text": "普通消息", "ts": T0, "expires_at": T0 + 86400, "score": 0.2, "reasons": []},
            ],
        }
        self.assertEqual([], extract_moment_portrait_candidates(moments, now=T0 + 10))

    def test_moment_evidence_stays_in_source_group(self):
        self.harness._settings["enable_group_moments"] = True
        self.harness._settings["enable_group_moment_portrait"] = True
        self.harness.data["users"] = {"u1": {"umo": "qq:FriendMessage:u1"}}
        self.harness.messages = [
            {"sender_id": "u1", "name": "漂", "text": "这谁顶得住", "ts": T0},
        ]
        group: dict = {}
        self.harness._update_group_social_context(group, now=T0)
        user = self.harness.data["users"]["u1"]
        self.assertNotIn("companion_memory", user)
        self.assertTrue(group["moment_portrait_candidates"])
        sections = []
        self.harness._append_group_social_context_sections(group, sections, sender_id="u1", now=T0)
        evidence = next(s for s in sections if s.key == "group.moment_interaction_evidence")
        self.assertIn("单次互动线索", evidence.content)
        self.assertIn("不能据此推定长期偏好", evidence.content)
        for other_group, sender, now in (({}, "u1", T0), (group, "u2", T0), (group, "u1", T0 + 8 * 86400)):
            sections = []
            self.harness._append_group_social_context_sections(other_group, sections, sender_id=sender, now=now)
            self.assertNotIn("group.moment_interaction_evidence", {s.key for s in sections})
        self.harness._settings["enable_group_moment_portrait"] = False
        sections = []
        self.harness._append_group_social_context_sections(group, sections, sender_id="u1", now=T0)
        self.assertNotIn("group.moment_interaction_evidence", {s.key for s in sections})
        self.harness._update_group_social_context(group, now=T0)
        self.assertNotIn("moment_portrait_candidates", group)

    def test_settle_moment_portraits_does_not_write_unknown_users(self):
        self.harness._settings["enable_group_moments"] = True
        self.harness._settings["enable_group_moment_portrait"] = True
        self.harness.messages = [
            {"sender_id": "ghost", "name": "幽灵", "text": "经典名场面", "ts": T0},
        ]
        group: dict = {}
        self.harness._update_group_social_context(group, now=T0)
        self.assertNotIn("ghost", self.harness.data["users"])


if __name__ == "__main__":
    unittest.main()
