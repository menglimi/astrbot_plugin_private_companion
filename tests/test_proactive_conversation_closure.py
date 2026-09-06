import unittest

from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _MemoryHarness(UserMemoryMixin):
    proactive_closing_grace_minutes = 30


class _EngineHarness(ProactiveEngineMixin):
    proactive_closing_grace_minutes = 30

    @staticmethod
    def _candidate_trigger_message_id(candidate):
        return str(candidate.get("trigger_message_id") or "")


class _ImpulseHarness(ProactiveEngineMixin):
    @staticmethod
    def _private_user_role(_user):
        return "owner"

    @staticmethod
    def _normalize_internal_motive_text(value):
        return str(value or "")

    @staticmethod
    def _proactive_persona_alignment(*_args, **_kwargs):
        return {"score": 0.8, "note": "ok", "blocker": False}

    @staticmethod
    def _proactive_candidate_semantics(*_args, **_kwargs):
        return {"kind": "care", "anchor_type": "time", "score": 0.8}

    @staticmethod
    def _proactive_message_kind(**_kwargs):
        return "relational"

    @staticmethod
    def _proactive_kind_policy(_kind):
        return {"label": "关系触达", "response_expectation": "optional"}

    @staticmethod
    def _proactive_quota_policy(_user):
        return {"tier": 1, "label": "normal"}

    @staticmethod
    def _proactive_topic_signature(*_args):
        return "sig"


class ProactiveConversationClosureTests(unittest.TestCase):
    def test_closing_posture_is_recorded_without_text_matching(self):
        harness = _MemoryHarness()
        user = {"planned_proactive_conversation_posture": "closing"}

        recorded = harness._record_proactive_conversation_closing(
            user,
            source="daily_greeting",
            reason="evening_greeting",
            motive="想把一句话放下",
            now=100.0,
        )

        self.assertTrue(recorded)
        self.assertEqual(user["state_continuity"]["conversation_closing"]["until"], 1900.0)
        self.assertEqual(user["state_continuity"]["conversation_closing"]["posture"], "closing")

    def test_non_closing_posture_does_not_create_gate(self):
        harness = _MemoryHarness()
        user = {"planned_proactive_conversation_posture": "open"}

        self.assertFalse(harness._record_proactive_conversation_closing(user, now=100.0))
        self.assertNotIn("state_continuity", user)

    def test_user_activity_supersedes_and_expiry_releases_closing(self):
        harness = _EngineHarness()
        user = {
            "state_continuity": {
                "conversation_closing": {"at": 100.0, "until": 1900.0, "posture": "closing"}
            },
            "planned_proactive_conversation_closing_deferred": True,
            "next_proactive_at": 1900.0,
            "planned_proactive_window_start_at": 1900.0,
            "proactive_impulses": [
                {"conversation_closing_deferred": True, "window_start_at": 1900.0, "preferred_ts": 1900.0}
            ],
        }

        self.assertEqual(harness._proactive_conversation_closing_until(user, now=500.0), 1900.0)
        user["last_private_activity_at"] = 501.0
        self.assertEqual(harness._proactive_conversation_closing_until(user, now=500.0), 0.0)
        self.assertEqual(user["next_proactive_at"], 500.0)
        self.assertEqual(user["proactive_impulses"][0]["window_start_at"], 500.0)
        user["last_private_activity_at"] = 0.0
        self.assertEqual(harness._proactive_conversation_closing_until(user, now=1900.0), 0.0)

    def test_confirmed_visible_departure_is_compatible_with_closing_gate(self):
        harness = _EngineHarness()
        user = {"conversation_departure": {"at": 100.0, "kind": "bot_initiated_close"}}

        self.assertEqual(harness._proactive_conversation_closing_until(user, now=500.0), 1900.0)

    def test_routine_candidates_shift_but_explicit_and_timely_candidates_pass(self):
        harness = _EngineHarness()
        user = {
            "state_continuity": {
                "conversation_closing": {"at": 100.0, "until": 1900.0, "posture": "closing"}
            },
        }
        candidate = {
            "source": "open_loop",
            "scheduled_ts": 500.0,
            "window_start_at": 500.0,
            "preferred_ts": 600.0,
            "best_until_at": 900.0,
            "expire_at": 1200.0,
        }

        shifted = harness._defer_candidate_after_conversation_closing(
            user, candidate, now=500.0, timeliness="routine"
        )
        self.assertEqual(shifted["scheduled_ts"], 1900.0)
        self.assertEqual(shifted["expire_at"], 2600.0)
        self.assertTrue(shifted["conversation_closing_deferred"])

        triggered = dict(candidate, trigger_message_id="msg-1")
        self.assertIs(harness._defer_candidate_after_conversation_closing(user, triggered, now=500.0), triggered)
        timely = harness._defer_candidate_after_conversation_closing(
            user, candidate, now=500.0, timeliness="timely"
        )
        self.assertIs(timely, candidate)

    def test_impulse_keeps_semantic_conversation_posture(self):
        harness = _ImpulseHarness()
        impulse = harness._build_proactive_impulse(
            {},
            reason="evening_greeting",
            action="message",
            motive="短短说一句",
            topic="晚间问候",
            source="daily_greeting",
            window_start_at=100.0,
            preferred_ts=100.0,
            best_until_at=200.0,
            expire_at=300.0,
            conversation_posture="closing",
        )
        self.assertEqual(impulse["conversation_posture"], "closing")


if __name__ == "__main__":
    unittest.main()
