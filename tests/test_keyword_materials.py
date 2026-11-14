from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import unittest

from companion_v2.conversation.keyword_materials import (
    KeywordMaterialSpec,
    KeywordMaterials,
    render,
)

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


class Context:
    def __init__(self, private=True):
        self.private = private


def spec(calls, *, owner="service.balance", keywords=("余额",), available=None, payload=None,
         error=None, ttl=300):
    async def prepare(context, text, at):
        calls.append((owner, at))
        if error is not None:
            raise error
        return payload if payload is not None else {"state": "ok", "balance": "12.34"}
    return KeywordMaterialSpec(owner=owner, title="服务余额", instruction="只作参考",
                               keywords=keywords, prepare=prepare,
                               available=available, ttl_seconds=ttl)


class KeywordMaterialsTests(unittest.TestCase):
    def test_only_the_current_message_triggers_a_fetch(self):
        calls = []
        registry = KeywordMaterials((spec(calls),))
        asyncio.run(registry.prepare(Context(), "今天天气怎么样", at=NOW))
        self.assertEqual(calls, [])
        entries = asyncio.run(registry.prepare(Context(), "看看还有多少余额", at=NOW))
        self.assertEqual(len(calls), 1)
        self.assertEqual(entries[0]["owner"], "service.balance")
        self.assertEqual(entries[0]["payload"]["balance"], "12.34")

    def test_unavailable_source_is_never_queried(self):
        calls = []
        registry = KeywordMaterials((spec(calls, available=lambda context: False),))
        self.assertEqual(asyncio.run(registry.prepare(Context(), "余额多少", at=NOW)), ())
        self.assertEqual(calls, [])

        def broken(context):
            raise RuntimeError("boom")
        registry = KeywordMaterials((spec(calls, available=broken),))
        self.assertEqual(asyncio.run(registry.prepare(Context(), "余额多少", at=NOW)), ())
        self.assertEqual(calls, [])

    def test_failure_is_injected_with_a_reason_instead_of_vanishing(self):
        calls = []
        registry = KeywordMaterials((spec(calls, keywords=("余额", "额度"), error=TimeoutError()),))
        entries = asyncio.run(registry.prepare(Context(), "还有多少额度", at=NOW))
        self.assertEqual(entries[0]["payload"], {"state": "unavailable", "code": "TimeoutError"})
        self.assertIn("unavailable", render(entries))

    def test_successful_payload_is_cached_for_its_ttl(self):
        calls = []
        registry = KeywordMaterials((spec(calls, ttl=300),))
        asyncio.run(registry.prepare(Context(), "余额", at=NOW))
        asyncio.run(registry.prepare(Context(), "余额还有多少", at=NOW + timedelta(seconds=120)))
        self.assertEqual(len(calls), 1)
        asyncio.run(registry.prepare(Context(), "余额", at=NOW + timedelta(seconds=301)))
        self.assertEqual(len(calls), 2)

    def test_failures_are_cached_briefly_so_one_outage_does_not_hammer_the_source(self):
        calls = []
        registry = KeywordMaterials((spec(calls, error=RuntimeError("down")),))
        asyncio.run(registry.prepare(Context(), "余额", at=NOW))
        asyncio.run(registry.prepare(Context(), "余额", at=NOW + timedelta(seconds=30)))
        self.assertEqual(len(calls), 1)
        asyncio.run(registry.prepare(Context(), "余额", at=NOW + timedelta(seconds=90)))
        self.assertEqual(len(calls), 2)

    def test_at_most_three_materials_per_turn(self):
        calls = []
        registry = KeywordMaterials(tuple(
            spec(calls, owner=f"owner.{index}", keywords=("余额",)) for index in range(5)))
        entries = asyncio.run(registry.prepare(Context(), "余额", at=NOW))
        self.assertEqual(len(entries), 3)

    def test_render_quotes_the_payload_and_keeps_the_instruction(self):
        entries = ({"owner": "service.balance", "title": "服务余额",
                    "instruction": "不是对方账户余额", "payload": {"state": "ok", "balance": "1.00"}},)
        text = render(entries)
        self.assertIn("【服务余额】", text)
        self.assertIn("不是对方账户余额", text)
        self.assertIn('"balance": "1.00"', text)

    def test_invalidate_discards_only_the_named_source(self):
        calls = []
        registry = KeywordMaterials((spec(calls), spec(calls, owner="other.source")))
        asyncio.run(registry.prepare(Context(), "余额", at=NOW))
        registry.invalidate("service.balance")
        asyncio.run(registry.prepare(Context(), "余额", at=NOW + timedelta(seconds=1)))
        self.assertEqual([owner for owner, _ in calls],
                         ["service.balance", "other.source", "service.balance"])

    def test_invalidated_inflight_result_cannot_overwrite_a_new_source_cache(self):
        async def exercise():
            started, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def prepare(context, text, at):
                calls.append(text)
                if len(calls) == 1:
                    started.set()
                    await release.wait()
                    return {"state": "unavailable", "code": "balance_source_changed"}
                return {"state": "ok", "balance": "2.00"}
            registry = KeywordMaterials((KeywordMaterialSpec(owner="service.balance", title="余额",
                instruction="只作参考", keywords=("余额",), prepare=prepare, ttl_seconds=300),))
            pending = asyncio.create_task(registry.prepare(Context(), "旧余额", at=NOW))
            await started.wait()
            registry.invalidate("service.balance")
            await registry.prepare(Context(), "新余额", at=NOW + timedelta(seconds=1))
            release.set()
            await pending
            current = await registry.prepare(Context(), "余额", at=NOW + timedelta(seconds=2))
            self.assertEqual(current[0]["payload"]["balance"], "2.00")
            self.assertEqual(calls, ["旧余额", "新余额"])
        asyncio.run(exercise())

    def test_spec_validation(self):
        calls = []
        with self.assertRaises(ValueError):
            spec(calls, keywords=())
        with self.assertRaises(ValueError):
            spec(calls, owner="")
        with self.assertRaises(ValueError):
            spec(calls, ttl=-1)
        with self.assertRaises(ValueError):
            KeywordMaterials((spec(calls), spec(calls)))


if __name__ == "__main__":
    unittest.main()
