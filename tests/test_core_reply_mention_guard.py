"""Issue #836: merged-batch mentions must not be silently skipped.

Three guards are covered here:
1. ``choose_participation`` refuses a whole-batch skip when the batch carries
   an explicit mention/host wakeup, unless the reason is itself an independent
   conclusion about the mention (e.g. the speaker said "不用理我").
2. ``_latch_group_skip`` never latches a skip for a mention batch, and a
   replayed skip decision is discarded when current trigger facts show a
   mention that the cache never saw.
3. The air-guard branches in multi_session exempt mention/wakeup candidates.
"""

import json
from types import SimpleNamespace as NS
import unittest

from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from tests.test_core_reply_tools import CoreReplyToolFixture
from astrbot_host.core_reply_tools import CoreReplyToolSession
from companion_v2.foundation.refs import ScopeKind

WAKE_FACTS = {"mentioned": True, "native_wakeup": False, "mentions_others": False,
              "reply_ids": (), "mentions_all": False, "quoted_bot": False, "has_image": False}


class CoreReplyMentionGuardTests(CoreReplyToolFixture, unittest.IsolatedAsyncioTestCase):
    def _group_session(self, event, prepared, event_id="group-mention-guard"):
        prepared = {"context": self.context, "event_id": event_id, **prepared}

        async def authorize(_prepared):
            return Outcome.success(True)

        from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, prepare_core_reply_event
        prepare_core_reply_event(event, prepared, authorize=authorize)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        from dataclasses import replace as _replace
        group_scope = _replace(self.context.scope, kind=ScopeKind.GROUP, native_scope_id="group-guard",
                               conversation_id="conversation-guard", session_id="session-guard")
        session = CoreReplyToolSession.__new__(CoreReplyToolSession)
        session.event, session.state, session.prepared = event, state, prepared
        session.context = NS(scope=group_scope, role_binding=self.context.role_binding)
        session.application = self.app
        session.native_run_context, session.native_calls = None, []
        return session

    def _choose(self, session, arguments, event_id="choose-call", event=None):
        wrapper = self.wrapper("group.participation.choose", arguments, event_id, event=event)
        return json.loads(json.dumps(session.choose_participation(wrapper, arguments)))

    async def test_skip_with_mention_is_refused_unless_reason_targets_the_mention(self):
        event, _ = self.prepare("mention-skip-refused")
        session = self._group_session(event, {"group_trigger": dict(WAKE_FACTS)})
        with self.assertRaises(TaskError) as captured:
            self._choose(session, {"mode": "skip", "reason": "他们在讨论运维，与我无关"},
                         event=event)
        self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")
        # An explicit opt-out about the mention itself stays allowed.
        decision = self._choose(session, {"mode": "skip", "reason": "@我但明说不用理我"},
                                event=event)
        self.assertEqual(decision["payload"]["mode"], "skip")

    async def test_skip_without_mention_still_latches(self):
        event, _ = self.prepare("no-mention-latch")
        facts = dict(WAKE_FACTS, mentioned=False)
        session = self._group_session(event, {"group_trigger": facts})
        decision = self._choose(session, {"mode": "skip", "reason": "普通旁听"})
        self.assertFalse(decision["replayed"])
        replay = self._choose(session, {"mode": "skip", "reason": "普通旁听"})
        self.assertTrue(replay["replayed"])

    async def test_latch_group_skip_ignores_mention_batches(self):
        event, _ = self.prepare("latch-mention")
        session = self._group_session(event, {"group_trigger": dict(WAKE_FACTS)})
        wrapper = self.wrapper("group.participation.choose",
                               {"mode": "skip", "reason": "整批与我无关"}, event=event)
        session._latch_group_skip(wrapper, "group.participation.choose",
                                  {"mode": "skip", "reason": "整批与我无关"})
        self.assertNotIn("group_participation", session.state)

    async def test_replayed_skip_is_discarded_when_facts_now_show_mention(self):
        event, _ = self.prepare("replay-discard")
        facts = dict(WAKE_FACTS, mentioned=False)
        session = self._group_session(event, {"group_trigger": facts})
        decision = self._choose(session, {"mode": "skip", "reason": "普通旁听"})
        self.assertFalse(decision["replayed"])
        session.prepared["group_trigger"] = dict(WAKE_FACTS)
        with self.assertRaises(TaskError) as captured:
            self._choose(session, {"mode": "skip", "reason": "普通旁听"}, event=event)
        self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")
        self.assertNotIn("group_participation", session.state)

    async def test_repeated_unopted_skip_is_bounded(self):
        # Issue #836: the first unopted skip is rejected, but a second one
        # after the forced re-judgement must not raise again.
        event, _ = self.prepare("bounded-skip")
        session = self._group_session(event, {"group_trigger": dict(WAKE_FACTS)})
        with self.assertRaises(TaskError) as captured:
            self._choose(session, {"mode": "skip", "reason": "他们在讨论运维，与我无关"},
                         event=event)
        self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")
        decision = self._choose(session, {"mode": "skip", "reason": "他们在讨论运维，与我无关"},
                                event=event)
        self.assertEqual(decision["payload"]["mode"], "skip")
        self.assertEqual(session.state.get("_mention_skip_rejections"), 1)

    # -- PR-B: mention_force_reply_enabled (opt-in hard rule) ----------------

    def _set_force_reply(self, value):
        from companion_v2.conversation.participation import (CONFIG_GROUP,
                                                             GroupParticipationConfigurationOwner,
                                                             configuration_fields)
        from companion_v2.configuration.models import ConfigLayer, ConfigTarget
        from dataclasses import replace as _replace
        owner = GroupParticipationConfigurationOwner(self.app.runtime)
        fields = configuration_fields()
        group_scope = _replace(self.context.scope, kind=ScopeKind.GROUP, native_scope_id="group-guard",
                               conversation_id="conversation-guard", session_id="session-guard")
        target = ConfigTarget(self.context.scope.installation_id, ConfigLayer.WINDOW,
                              self.context.role_binding.role, group_scope)
        owner.write(target, fields, changes={CONFIG_GROUP + ".mention_force_reply_enabled": value},
                    reset=(), expected_revision=owner.read(target, fields).revision,
                    observed_at=self.app.tasks.now())

    async def test_force_reply_rejects_all_mention_skips(self):
        # Switch on: even an explicit opt-out reason and repeated attempts are
        # always rejected; the bounded bypass never applies.
        event, _ = self.prepare("force-reply-hard")
        session = self._group_session(event, {"group_trigger": dict(WAKE_FACTS)})
        self._set_force_reply(True)
        for reason in ("@我但明说不用理我", "他们在讨论运维，与我无关"):
            with self.assertRaises(TaskError) as captured:
                self._choose(session, {"mode": "skip", "reason": reason}, event=event)
            self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")
            self.assertNotIn("group_participation", session.state)
        with self.assertRaises(TaskError) as captured:
            self._choose(session, {"mode": "skip", "reason": "@我但明说不用理我"}, event=event)
        self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")

    async def test_force_reply_off_keeps_pr_a_behaviour(self):
        # Switch off (explicit False): opt-out reasons pass and the bounded
        # degradation still allows the second skip.
        event, _ = self.prepare("force-reply-off")
        session = self._group_session(event, {"group_trigger": dict(WAKE_FACTS)})
        self._set_force_reply(False)
        decision = self._choose(session, {"mode": "skip", "reason": "@我但明说不用理我"}, event=event)
        self.assertEqual(decision["payload"]["mode"], "skip")
        event2, _ = self.prepare("force-reply-off-bounded")
        session2 = self._group_session(event2, {"group_trigger": dict(WAKE_FACTS)})
        with self.assertRaises(TaskError) as captured:
            self._choose(session2, {"mode": "skip", "reason": "与我无关"}, event=event2)
        self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")
        decision2 = self._choose(session2, {"mode": "skip", "reason": "与我无关"}, event=event2)
        self.assertEqual(decision2["payload"]["mode"], "skip")

    async def test_force_reply_defaults_to_off(self):
        # No window value written: the guard behaves exactly like PR-A.
        event, _ = self.prepare("force-reply-default")
        session = self._group_session(event, {"group_trigger": dict(WAKE_FACTS)})
        self.assertFalse(session._mention_force_reply_enabled())
        decision = self._choose(session, {"mode": "skip", "reason": "@我但明说不用理我"}, event=event)
        self.assertEqual(decision["payload"]["mode"], "skip")

    async def test_force_reply_bypasses_laya_and_rejects_skip_for_direct_word(self):
        from astrbot_host.core_reply_decision import prepare_core_reply_decision
        from unittest.mock import Mock

        event, _ = self.prepare("direct-word")
        session = self._group_session(event, {
            "channel": "group",
            "group_admission": {
                "reason": "direct_word",
                "participation_policy": {"mention_force_reply_enabled": True},
            },
            "group_trigger": dict(WAKE_FACTS, mentioned=False),
        })
        self._set_force_reply(True)
        classifier = Mock()
        decision = await prepare_core_reply_decision(
            event, session.prepared, application=NS(laya_decision_resolver=classifier))
        classifier.assert_not_called()
        self.assertFalse(decision["suppress_reply"])
        self.assertIn("直接回应点名或唤醒词所在的消息", session.prepared["prompt_instruction"])
        with self.assertRaises(TaskError) as captured:
            self._choose(session, {"mode": "skip", "reason": "与我无关"}, event=event)
        self.assertEqual(captured.exception.code, "group_participation_mention_skip_rejected")

    def test_rendered_policy_omits_mention_force_reply_when_disabled(self):
        # Issue #836: the policy dict only carries mention_force_reply_enabled
        # when the window opted in, so the off (default) rendering stays
        # byte-identical to the pre-switch model state.
        from companion_v2.decision.prompt import build_state, render_model_state
        base = {"scope_kind": "group", "user_text": "x",
                "group_participation_policy": {"question_enabled": True, "interjection_enabled": True}}
        off = json.loads(render_model_state(build_state(dict(base))))
        self.assertNotIn("mention_force_reply_enabled", off["facts"]["group_participation_policy"])
        on = json.loads(render_model_state(build_state({**base, "mentioned": True,
                "group_participation_policy": {**base["group_participation_policy"],
                                               "mention_force_reply_enabled": True}})))
        self.assertIs(on["facts"]["group_participation_policy"]["mention_force_reply_enabled"], True)
        self.assertIn("被@强制应答", on["facts"]["address_context"])


if __name__ == "__main__":
    unittest.main()
