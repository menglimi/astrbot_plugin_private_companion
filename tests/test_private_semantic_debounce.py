import asyncio
import unittest

from astrbot_host.private_debounce import PrivateDebounce
from companion_v2.conversation.private_debounce import Policy


class Classifier:
    threshold = 0.8
    wait_seconds = 1

    def __init__(self, score):
        self.value = score
        self.called = asyncio.Event()

    async def score(self, text, remaining):
        self.called.set()
        return self.value

    def log_decision(self, score, incomplete, count):
        pass


class SemanticDebounceTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_input_waits_for_followup(self):
        classifier = Classifier(0.1)
        policy = Policy(min_seconds=.01, max_seconds=.01, total_seconds=1,
                        semantic_enabled=True)
        manager = PrivateDebounce(policy, semantic=classifier, log_enabled=False)
        first, second = object(), object()
        a = manager.reserve("window", first, "我想问")
        waiting = asyncio.create_task(manager.wait_for_quiet(a, first))
        await asyncio.wait_for(classifier.called.wait(), .5)
        self.assertFalse(waiting.done())
        classifier.value = .99
        b = manager.reserve("window", second, "明天几点出门")
        self.assertEqual(await waiting, "superseded")
        self.assertEqual(await manager.wait_for_quiet(b, second), "ready")
        entries = await manager.finish(b, second)
        self.assertEqual([entry[1] for entry in entries], ["我想问", "明天几点出门"])

    async def test_disabled_does_not_call_classifier(self):
        classifier = Classifier(0.1)
        manager = PrivateDebounce(Policy(min_seconds=.01, max_seconds=.01),
                                  semantic=classifier, log_enabled=False)
        event = object()
        reservation = manager.reserve("window", event, "问你个事")
        self.assertEqual(await manager.wait_for_quiet(reservation, event), "ready")
        self.assertFalse(classifier.called.is_set())

    async def test_unavailable_classifier_keeps_original_wait(self):
        classifier = Classifier(None)
        policy = Policy(min_seconds=.01, max_seconds=.01, semantic_enabled=True)
        manager = PrivateDebounce(policy, semantic=classifier, log_enabled=False)
        event = object()
        reservation = manager.reserve("window", event, "问你个事")
        self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(reservation, event), .5), "ready")
        self.assertTrue(classifier.called.is_set())
