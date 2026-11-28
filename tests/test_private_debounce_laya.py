"""Unit tests for three-mode private semantic debounce, Laya single-head completeness, and readiness API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from astrbot_host.laya_client import LayaDecisionClient
from astrbot_host.private_debounce import MAX_SEMANTIC_JUDGMENTS_PER_TURN, PrivateDebounce
from astrbot_host.private_semantic_debounce import (
    LayaTurnCompleteness,
    fast_path_complete_reason,
    semantic_debounce_status,
)
from astrbot_host.reply_api import ReplyWebAPI
from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.conversation.private_debounce import DEFAULTS, Policy, resolve_semantic_mode
from companion_v2.conversation.reply_settings import (
    GROUP,
    ReplyConfigurationOwner,
    configuration_fields,
    private_debounce_settings,
    validate,
)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.decision.client import LayaClientError
from companion_v2.decision.service import LayaDecisionService
from companion_v2.decision.store import LayaSettingsStore
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.storage.runtime import StorageRuntime
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_management_access import NOW


def _base_reply_values(**extra):
    from dataclasses import asdict
    return {**asdict(SegmentationPolicy()), "streaming": False, "part_gap_ms": 0, **extra}


class SemanticDebounceModeCompatibilityTests(unittest.TestCase):
    def test_resolve_mode_derives_from_legacy_boolean_and_rejects_invalid(self):
        self.assertEqual(resolve_semantic_mode(semantic_mode=None, semantic_enabled=False), "off")
        self.assertEqual(resolve_semantic_mode(semantic_mode=None, semantic_enabled=True), "onnx")
        self.assertEqual(resolve_semantic_mode(semantic_mode="laya", semantic_enabled=False), "laya")
        self.assertEqual(resolve_semantic_mode(semantic_mode="off", semantic_enabled=True), "off")
        with self.assertRaises(ValueError):
            resolve_semantic_mode(semantic_mode="unknown")
        with self.assertRaises(ValueError):
            resolve_semantic_mode(semantic_mode=None, semantic_enabled="yes")

    def test_policy_and_reply_settings_preserve_legacy_and_support_three_modes(self):
        default = private_debounce_settings({})
        self.assertEqual(default.semantic_mode, "off")
        self.assertFalse(default.semantic_enabled)

        legacy_on = private_debounce_settings({"private_debounce_semantic_enabled": True})
        self.assertEqual(legacy_on.semantic_mode, "onnx")
        self.assertTrue(legacy_on.semantic_enabled)

        laya = private_debounce_settings({"private_debounce_semantic_mode": "laya"})
        self.assertEqual(laya.semantic_mode, "laya")
        self.assertTrue(laya.semantic_enabled)

        direct_legacy = Policy(semantic_enabled=True)
        self.assertEqual(direct_legacy.semantic_mode, "onnx")
        direct_laya = Policy(semantic_mode="laya")
        self.assertTrue(direct_laya.semantic_enabled)

        with self.assertRaises(ConfigurationError):
            private_debounce_settings({"private_debounce_semantic_mode": "invalid"})

        # Legacy snapshots containing only timing + boolean, or timing + mode, stay valid.
        timing = {k: DEFAULTS[k] for k in ("private_debounce_enabled", "private_debounce_min_ms",
                                           "private_debounce_max_ms", "private_debounce_total_ms")}
        validate(_base_reply_values(**timing))
        validate(_base_reply_values(**timing, private_debounce_semantic_enabled=True))
        validate(_base_reply_values(**timing, private_debounce_semantic_mode="laya"))

    def test_owner_read_derives_mode_from_legacy_explicit_boolean_and_syncs_writes(self):
        with TemporaryDirectory(prefix="companion-v2-debounce-mode-") as directory:
            runtime = StorageRuntime.open(data_root=Path(directory) / "runtime", installation_id="debounce-owner")
            try:
                fields = configuration_fields(SegmentationPolicy(), False, 0)
                owner = ReplyConfigurationOwner(runtime)
                role = SubjectRef("debounce-owner", SubjectKind.BOT, "role-a")
                scope = ScopeRef("debounce-owner", "onebot", ScopeKind.PRIVATE, "u-1", "bot-1", "acc-1", "conv-1", "sess-1")
                win_target = ConfigTarget("debounce-owner", ConfigLayer.WINDOW, role, scope, bot_id="bot-1")

                # Simulate legacy DB write where only private_debounce_semantic_enabled was explicitly saved.
                super(ReplyConfigurationOwner, owner).write(
                    win_target, fields,
                    changes={GROUP + ".private_debounce_semantic_enabled": True},
                    reset=(), expected_revision=0, observed_at=NOW,
                )
                snap = owner.read(win_target, fields)
                self.assertEqual(snap.fields[GROUP + ".private_debounce_semantic_mode"].value, "onnx")
                self.assertTrue(snap.fields[GROUP + ".private_debounce_semantic_mode"].explicit)

                # Switching to laya writes explicit mode="laya" and keeps legacy bool False (safe rollback).
                owner.write(
                    win_target, fields,
                    changes={GROUP + ".private_debounce_semantic_mode": "laya"},
                    reset=(), expected_revision=snap.revision, observed_at=NOW,
                )
                snap2 = owner.read(win_target, fields)
                self.assertEqual(snap2.fields[GROUP + ".private_debounce_semantic_mode"].value, "laya")
                self.assertFalse(snap2.fields[GROUP + ".private_debounce_semantic_enabled"].value)

                # Resetting mode clears both mode and legacy boolean override.
                owner.write(
                    win_target, fields,
                    changes={},
                    reset=(GROUP + ".private_debounce_semantic_mode",),
                    expected_revision=snap2.revision, observed_at=NOW,
                )
                snap3 = owner.read(win_target, fields)
                self.assertEqual(snap3.fields[GROUP + ".private_debounce_semantic_mode"].value, "off")
                self.assertFalse(snap3.fields[GROUP + ".private_debounce_semantic_mode"].explicit)
                self.assertFalse(snap3.fields[GROUP + ".private_debounce_semantic_enabled"].explicit)

                # Cross-layer ROLE -> WINDOW inheritance cases:
                role_target = ConfigTarget("debounce-owner", ConfigLayer.ROLE, role, bot_id="bot-1")
                # 1. ROLE legacy enabled=True + WINDOW empty -> WINDOW inherits mode="onnx" (source=role, explicit=False).
                super(ReplyConfigurationOwner, owner).write(
                    role_target, fields,
                    changes={GROUP + ".private_debounce_semantic_enabled": True},
                    reset=(), expected_revision=0, observed_at=NOW,
                )
                win_inherit_legacy = owner.read(win_target, fields)
                mode_field = win_inherit_legacy.fields[GROUP + ".private_debounce_semantic_mode"]
                self.assertEqual(mode_field.value, "onnx")
                self.assertEqual(mode_field.source, role_target)
                self.assertFalse(mode_field.explicit)

                # 2. WINDOW explicit mode="laya" overrides ROLE legacy enabled=True.
                owner.write(
                    win_target, fields,
                    changes={GROUP + ".private_debounce_semantic_mode": "laya"},
                    reset=(), expected_revision=win_inherit_legacy.revision, observed_at=NOW,
                )
                win_override = owner.read(win_target, fields)
                self.assertEqual(win_override.fields[GROUP + ".private_debounce_semantic_mode"].value, "laya")
                self.assertTrue(win_override.fields[GROUP + ".private_debounce_semantic_mode"].explicit)

                # Reset WINDOW override -> falls back to ROLE legacy enabled=True ("onnx").
                owner.write(
                    win_target, fields,
                    changes={},
                    reset=(GROUP + ".private_debounce_semantic_mode",),
                    expected_revision=win_override.revision, observed_at=NOW,
                )
                self.assertEqual(owner.read(win_target, fields).fields[GROUP + ".private_debounce_semantic_mode"].value, "onnx")

                # 3. ROLE new mode="laya" (syncs enabled=False) + WINDOW empty -> WINDOW inherits mode="laya".
                role_snap = owner.read(role_target, fields)
                owner.write(
                    role_target, fields,
                    changes={GROUP + ".private_debounce_semantic_mode": "laya"},
                    reset=(), expected_revision=role_snap.revision, observed_at=NOW,
                )
                win_inherit_laya = owner.read(win_target, fields)
                mode_laya = win_inherit_laya.fields[GROUP + ".private_debounce_semantic_mode"]
                self.assertEqual(mode_laya.value, "laya")
                self.assertEqual(mode_laya.source, role_target)
                self.assertFalse(mode_laya.explicit)

                # 4. ROLE new mode="laya" + WINDOW legacy explicit enabled=True -> WINDOW overrides to "onnx".
                super(ReplyConfigurationOwner, owner).write(
                    win_target, fields,
                    changes={GROUP + ".private_debounce_semantic_enabled": True},
                    reset=(), expected_revision=win_inherit_laya.revision, observed_at=NOW,
                )
                win_legacy_override = owner.read(win_target, fields)
                mode_win_legacy = win_legacy_override.fields[GROUP + ".private_debounce_semantic_mode"]
                self.assertEqual(mode_win_legacy.value, "onnx")
                self.assertEqual(mode_win_legacy.source, win_target)
                self.assertTrue(mode_win_legacy.explicit)
            finally:
                runtime.close()


class FastPathTests(unittest.TestCase):
    def test_fast_path_skips_long_terminal_and_short_budget_but_keeps_hesitant_and_short(self):
        self.assertEqual(fast_path_complete_reason("我想问", 0.25), "budget_too_short")
        self.assertEqual(fast_path_complete_reason("我想问", 0.3), "budget_too_short")
        self.assertEqual(fast_path_complete_reason("   ", 5.0), "empty")
        self.assertEqual(fast_path_complete_reason("明天几点出门？", 5.0), "terminal_punctuation")
        self.assertEqual(fast_path_complete_reason("好啦我先睡了。", 5.0), "terminal_punctuation")
        self.assertEqual(fast_path_complete_reason("收到!", 5.0), "terminal_punctuation")
        long_text = "这是一段已经表达非常完整而且字数超过四十个可见字符的普通私聊内容不需要再问决策模型"
        self.assertEqual(fast_path_complete_reason(long_text, 5.0), "long_complete")

        # Must NOT fast-path: short open fragments or hesitant trailing punctuation.
        self.assertIsNone(fast_path_complete_reason("跟你说个事", 5.0))
        self.assertIsNone(fast_path_complete_reason("等一下……", 5.0))
        self.assertIsNone(fast_path_complete_reason("等一下...", 5.0))
        self.assertIsNone(fast_path_complete_reason(long_text + "，", 5.0))
        self.assertIsNone(fast_path_complete_reason(long_text + "……", 5.0))


class LayaTurnCompletenessClientAndServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_single_head_turn_state_across_remote_jev_and_workers_ai(self):
        cases = [
            ("remote", "https://laya.example.test",
             json.dumps({"answers": {"turn_state": {"type": "choice", "choice": "unfinished"}}}), "unfinished"),
            ("jev", "https://gateway.example.test/v1/chat/completions",
             json.dumps({"choices": [{"message": {"content": json.dumps(
                 {"answers": {"turn_state": {"type": "choice", "choice": "complete"}}})}}]}), "complete"),
            ("workers_ai", "https://api.cloudflare.com/client/v4/accounts/acct/ai/run",
             json.dumps({"success": True, "result": {
                 "answers": {"turn_state": {"type": "choice", "choice": "uncertain"}}}}), "uncertain"),
        ]
        for mode, endpoint, raw_response, expected in cases:
            with self.subTest(mode=mode):
                captured = {}
                client = LayaDecisionClient(mode=mode, endpoint=endpoint)

                async def fake_post(state, questions):
                    captured["state"] = state
                    captured["questions"] = questions
                    return raw_response

                client._post = fake_post
                choice = await client.classify_turn_completeness(
                    {"scope_kind": "private", "user_text": "跟你说个事", "input_parts": ["在吗", "跟你说个事"]},
                    timeout_seconds=1.5,
                )
                self.assertEqual(choice, expected)
                self.assertEqual(set(captured["questions"]), {"turn_state"})
                self.assertIn("跟你说个事", captured["state"])

    async def test_service_records_private_debounce_source_without_storing_chat_body(self):
        with TemporaryDirectory(prefix="companion-v2-debounce-ledger-") as directory:
            root = Path(directory)
            store = LayaSettingsStore(root / "settings")
            store.update(store.read()["revision"], {
                "enabled": True, "mode": "remote", "endpoint": "https://laya.example.test/v1/systemone",
                "remote_model": "laya-test-model", "timeout_seconds": 6,
            })
            runtime = StorageRuntime.open(data_root=root / "runtime", installation_id="debounce-laya-ledger")
            try:
                class FakeClient:
                    last_trace = {
                        "request": {"model": "laya-test-model", "sent": True,
                                    "state": '{"facts":{"user_text":"机密私聊文本"}}'},
                        "response": {"format": "json", "body": "机密私聊文本", "value": {}},
                        "processing": {"stages": [{"name": "inference", "status": "ok"}], "turn_state": "unfinished"},
                    }

                    async def classify_turn_completeness(self, facts, *, timeout_seconds=None):
                        self.seen_timeout = timeout_seconds
                        return "unfinished"

                fake_client = FakeClient()
                service = LayaDecisionService(store, runtime=runtime, origin_key="role-key")
                service._client = lambda _values: fake_client
                service.set_trace(event_ref="ev-debounce-1", source="private_debounce")

                result = await service.classify_turn_completeness(
                    {"scope_kind": "private", "user_text": "机密私聊文本"},
                    timeout_seconds=1.8,
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["turn_state"], "unfinished")
                self.assertEqual(fake_client.seen_timeout, 1.8)

                events = service.recent()["events"]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["source"], "private_debounce")
                self.assertEqual(events[0]["decision"], {"turn_state": "unfinished"})
                self.assertEqual(events[0]["scope_info"].get("kind"), "private")
                self.assertNotIn("机密私聊文本", json.dumps(events[0], ensure_ascii=False))
            finally:
                runtime.close()


class LayaPrivateDebounceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_laya_unfinished_extends_wait_and_followup_merges_complete(self):
        calls = []
        called = asyncio.Event()
        responses = iter([{"status": "ok", "turn_state": "unfinished"},
                          {"status": "ok", "turn_state": "complete"}])

        class FakeService:
            def __init__(self):
                self.store = SimpleNamespace(raw=lambda: {"enabled": True, "timeout_seconds": 6})
                self.trace_calls = []

            def set_trace(self, event_ref=None, source="conversation"):
                self.trace_calls.append((event_ref, source))

            async def classify_turn_completeness(self, facts, *, timeout_seconds=None):
                calls.append((facts, timeout_seconds))
                called.set()
                return next(responses)

        fake_service = FakeService()
        laya = LayaTurnCompleteness(lambda _ctx: fake_service, wait_seconds=10.0, max_timeout=2.0)
        onnx = SimpleNamespace(threshold=0.8, wait_seconds=10.0, score=AsyncMock(return_value=0.1), close=lambda: None)
        policy = Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=5.0, semantic_mode="laya")
        manager = PrivateDebounce(policy, semantic=onnx, laya_semantic=laya, log_enabled=False)
        self.addAsyncCleanup(manager.close)

        first, second = object(), object()
        r1 = manager.reserve("target", first, "跟你说个事")
        await manager.configure(r1, first, policy, context="ctx-1", event_ref="ev-1")
        waiting = asyncio.create_task(manager.wait_for_quiet(r1, first))
        await asyncio.wait_for(called.wait(), 1.0)
        self.assertFalse(waiting.done())
        self.assertEqual(fake_service.trace_calls[-1], ("ev-1", "private_debounce"))
        onnx.score.assert_not_awaited()

        r2 = manager.reserve("target", second, "明天一起吃饭")
        self.assertEqual(await asyncio.wait_for(waiting, 1.0), "superseded")
        self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(r2, second), 1.0), "ready")
        entries = await manager.finish(r2, second)
        self.assertEqual([item[1] for item in entries], ["跟你说个事", "明天一起吃饭"])
        self.assertEqual(len(calls), 2)
        self.assertLessEqual(calls[0][1], 2.0)

    async def test_laya_fast_path_skips_remote_call_at_zero_latency(self):
        fake_service = SimpleNamespace(
            store=SimpleNamespace(raw=lambda: {"enabled": True, "timeout_seconds": 6}),
            classify_turn_completeness=AsyncMock(),
        )
        laya = LayaTurnCompleteness(lambda _ctx: fake_service, wait_seconds=10.0, max_timeout=2.0)
        policy = Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=5.0, semantic_mode="laya")
        manager = PrivateDebounce(policy, laya_semantic=laya, log_enabled=False)
        self.addAsyncCleanup(manager.close)

        event = object()
        res = manager.reserve("target", event, "明天几点出门？")
        self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(res, event), 1.0), "ready")
        fake_service.classify_turn_completeness.assert_not_awaited()

    async def test_laya_check_incomplete_handles_none_or_invalid_remaining_safely(self):
        captured = {"calls": 0}

        class FakeService:
            store = SimpleNamespace(raw=lambda: {"enabled": True, "timeout_seconds": 6})

            def set_trace(self, **_kwargs):
                pass

            async def classify_turn_completeness(self, facts, *, timeout_seconds=None):
                captured["calls"] += 1
                captured["timeout"] = timeout_seconds
                return {"status": "ok", "turn_state": "unfinished"}

        laya = LayaTurnCompleteness(lambda _ctx: FakeService(), wait_seconds=10.0, max_timeout=1.7)
        self.assertTrue(await laya.check_incomplete("跟你说个事", None))
        self.assertEqual(captured["timeout"], 1.7)
        self.assertEqual(captured["calls"], 1)
        # Invalid or NaN remaining must fail-open safely without raising or calling Laya.
        self.assertFalse(await laya.check_incomplete("跟你说个事", "not-a-number"))
        self.assertFalse(await laya.check_incomplete("跟你说个事", float("nan")))
        self.assertEqual(captured["calls"], 1)

    async def test_laya_disabled_error_or_timeout_fails_open(self):
        for kind in ("disabled", "error", "timeout"):
            with self.subTest(kind=kind):
                if kind == "disabled":
                    service = SimpleNamespace(
                        store=SimpleNamespace(raw=lambda: {"enabled": False, "timeout_seconds": 6}),
                        classify_turn_completeness=AsyncMock(),
                    )
                elif kind == "error":
                    service = SimpleNamespace(
                        store=SimpleNamespace(raw=lambda: {"enabled": True, "timeout_seconds": 6}),
                        classify_turn_completeness=AsyncMock(side_effect=LayaClientError("laya_transport_failed", "down")),
                    )
                else:
                    async def slow(_facts, *, timeout_seconds=None):
                        await asyncio.sleep(5)
                        return {"status": "ok", "turn_state": "unfinished"}
                    service = SimpleNamespace(
                        store=SimpleNamespace(raw=lambda: {"enabled": True, "timeout_seconds": 6}),
                        classify_turn_completeness=AsyncMock(side_effect=slow),
                    )
                laya = LayaTurnCompleteness(lambda _ctx: service, wait_seconds=10.0, max_timeout=0.12)
                policy = Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=5.0, semantic_mode="laya")
                manager = PrivateDebounce(policy, laya_semantic=laya, log_enabled=False)
                self.addAsyncCleanup(manager.close)
                event = object()
                res = manager.reserve("target", event, "跟你说个事")
                self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(res, event), 1.5), "ready")
                if kind == "disabled":
                    service.classify_turn_completeness.assert_not_awaited()

    async def test_same_merged_text_and_turn_cap_prevent_redundant_laya_calls(self):
        calls = []

        class CountingLaya:
            wait_seconds = 0.02

            async def check_incomplete(self, text, remaining, **_kwargs):
                calls.append(text)
                return True

            def close(self):
                pass

        policy = Policy(min_seconds=0.01, max_seconds=0.01, total_seconds=5.0, semantic_mode="laya")
        manager = PrivateDebounce(policy, laya_semantic=CountingLaya(), log_enabled=False)
        self.addAsyncCleanup(manager.close)

        # Duplicate merged text (whitespace-only follow-up) must not re-call Laya.
        e1, e2 = object(), object()
        r1 = manager.reserve("same", e1, "跟你说个事")
        self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(r1, e1), 1.0), "ready")
        self.assertEqual(len(calls), 1)
        # Re-check on a fresh burst where an empty/whitespace update bumps version without changing combined text.
        calls.clear()
        e3, e4 = object(), object()
        r3 = manager.reserve("dup", e3, "跟你说个事")
        waiting = asyncio.create_task(manager.wait_for_quiet(r3, e3))
        await asyncio.sleep(0.015)
        r4 = manager.reserve("dup", e4, "   ")
        self.assertEqual(await asyncio.wait_for(waiting, 1.0), "superseded")
        self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(r4, e4), 1.0), "ready")
        self.assertEqual(len(calls), 1)

        # Multi-bubble turn cap: never exceed MAX_SEMANTIC_JUDGMENTS_PER_TURN calls.
        calls.clear()
        events = [object() for _ in range(MAX_SEMANTIC_JUDGMENTS_PER_TURN + 2)]
        res = manager.reserve("capped", events[0], "第0句")
        for idx in range(1, len(events)):
            t = asyncio.create_task(manager.wait_for_quiet(res, events[idx - 1]))
            await asyncio.sleep(0.015)
            res = manager.reserve("capped", events[idx], f"第{idx}句")
            await asyncio.wait_for(t, 1.0)
        self.assertEqual(await asyncio.wait_for(manager.wait_for_quiet(res, events[-1]), 1.0), "ready")
        self.assertLessEqual(len(calls), MAX_SEMANTIC_JUDGMENTS_PER_TURN)


class SemanticDebounceStatusAndReplyAPITests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def test_semantic_debounce_status_reports_onnx_and_laya_without_leaking_secrets(self):
        with TemporaryDirectory(prefix="companion-v2-debounce-status-") as directory:
            root = Path(directory)
            # Disabled Laya + empty ONNX model_dir.
            status0 = semantic_debounce_status({"private_debounce_semantic": {"model_dir": ""}}, data_root=root)
            self.assertEqual(status0["onnx"], {"ready": False, "reason": "model_dir_missing"})
            self.assertEqual(status0["laya"]["ready"], False)
            self.assertEqual(status0["laya"]["reason"], "disabled")

            # Enabled remote Laya with api_key + endpoint: ready=True, secrets and URLs omitted.
            store = LayaSettingsStore(root)
            store.update(store.read()["revision"], {
                "enabled": True, "mode": "remote",
                "endpoint": "https://secret-gateway.example.test/v1/systemone",
                "api_key": "sk-super-secret-value",
                "remote_model": "laya-test-model",
            })
            model_dir = root / "onnx_model"
            (model_dir / "tokenizer").mkdir(parents=True)
            (model_dir / "model.onnx").write_bytes(b"stub")
            (model_dir / "tokenizer" / "tokenizer.json").write_text("{}", encoding="utf-8")

            with patch("importlib.util.find_spec", side_effect=lambda name: object() if name in ("onnxruntime", "tokenizers") else None):
                status1 = semantic_debounce_status(
                    {"private_debounce_semantic": {"model_dir": str(model_dir)}}, data_root=root,
                )
            self.assertEqual(status1["onnx"], {"ready": True, "reason": "ready"})
            self.assertEqual(status1["laya"], {
                "ready": True, "mode": "remote", "model": "laya-test-model", "reason": "ready",
            })
            serialized = json.dumps(status1, ensure_ascii=False)
            self.assertNotIn("sk-super-secret-value", serialized)
            self.assertNotIn("secret-gateway.example.test", serialized)

            with patch("importlib.util.find_spec", return_value=None):
                status2 = semantic_debounce_status(
                    {"private_debounce_semantic": {"model_dir": str(model_dir)}}, data_root=root,
                )
            self.assertEqual(status2["onnx"], {"ready": False, "reason": "dependencies_missing"})

    async def test_reply_web_api_exposes_semantic_status_and_persists_laya_mode(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        store = LayaSettingsStore(self.controller.data_root)
        store.update(store.read()["revision"], {
            "enabled": True, "mode": "remote",
            "endpoint": "https://laya.example.test/v1/systemone",
            "api_key": "sk-private-key-do-not-expose",
            "remote_model": "laya-test-model",
        })
        await self.start_host()
        (await self.controller.authorize_delivery("first")).unwrap()
        self.controller.register_routes()
        handlers = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        reply_route = next(
            route for route, handler in handlers.items()
            if route.endswith("/reply") and handler.__self__.controller.target_id == "first"
            and handler.__self__.controller.role_id == "role-a"
        )

        async def call_api(handler, body=None):
            async def read():
                return json.dumps(body, ensure_ascii=False).encode("utf-8")
            with patch("astrbot_host.reply_api.request",
                       SimpleNamespace(username="reply-admin", query={}, body=read)):
                return await handler()

        status = (await call_api(handlers[reply_route]))["data"]
        self.assertEqual(status["settings"]["private_debounce_semantic_mode"], "off")
        self.assertEqual(status["private_debounce_semantic_status"]["laya"], {
            "ready": True, "mode": "remote", "model": "laya-test-model", "reason": "ready",
        })
        self.assertEqual(status["private_debounce_semantic_status"]["onnx"]["ready"], False)
        self.assertNotIn("sk-private-key-do-not-expose", json.dumps(status))
        self.assertNotIn("laya.example.test", json.dumps(status))

        saved = await call_api(handlers[reply_route + "/configure"], {
            "operation_key": "set-laya-debounce",
            "edit_token": status["edit_token"],
            "changes": {"private_debounce_semantic_mode": "laya"},
        })
        self.assertEqual(saved["status"], "ok", saved)
        self.assertEqual(saved["data"]["status"]["settings"]["private_debounce_semantic_mode"], "laya")
        self.assertEqual(saved["data"]["status"]["settings"]["private_debounce_semantic_enabled"], False)
        self.assertTrue(saved["data"]["status"]["private_debounce_semantic_status"]["laya"]["ready"])


if __name__ == "__main__":
    unittest.main()
