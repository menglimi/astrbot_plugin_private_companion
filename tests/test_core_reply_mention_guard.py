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
        session = CoreReplyToolSession.__new__(CoreReplyToolSession)
        session.event, session.state, session.prepared = event, state, prepared
        session.context = NS(scope=NS(kind=ScopeKind.GROUP))
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


if __name__ == "__main__":
    unittest.main()
