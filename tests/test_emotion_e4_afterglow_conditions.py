from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

try:
    import astrbot.api  # noqa: F401
except ImportError:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(debug=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api


def load_adapter():
    package_name = "emotion_e4_companion"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.memory_companion_adapter", ROOT / "memory_companion_adapter.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoryCompanionAdapterMixin


MemoryCompanionAdapterMixin = load_adapter()


class Event:
    unified_msg_origin = "qq:FriendMessage:user-1"

    def __init__(self, private: bool = True, sender_id: str = "user-1") -> None:
        self.private = private
        self.sender_id = sender_id

    def is_private_chat(self) -> bool:
        return self.private

    def get_sender_id(self) -> str:
        return self.sender_id


class Bridge:
    def __init__(self) -> None:
        self.capability = object()
        self.delivery_context = object()
        self.registered = None
        self.delivery_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.acked: list[dict[str, Any]] = []
        self.host: Any = None

    def register_emotion_producer(self, producer: Any) -> object:
        self.registered = producer
        return self.capability

    def create_emotion_delivery_context(self, capability: object, **kwargs: Any) -> object | None:
        if capability is not self.capability:
            return None
        self.delivery_calls.append(kwargs)
        return self.delivery_context

    async def list_emotion_events(self, *, delivery_context: object, **kwargs: Any) -> dict[str, Any]:
        if delivery_context is not self.delivery_context:
            raise AssertionError("delivery context is required")
        self.list_calls.append(kwargs)
        event = {
            "event_id": "emo-a",
            "revision": 1,
            "trace_id": "trace-a",
            "event_type": "warm_memory",
            "energy_delta": 4.0,
            "intensity": 80,
            "valence": 0.5,
            "arousal": 0.3,
            "vulnerability": 0.2,
            "confidence": 0.9,
        }
        return {"events": [event, dict(event)]}

    async def ack_emotion_events(self, refs: list[dict[str, Any]], *, delivery_context: object) -> dict[str, Any]:
        if delivery_context is not self.delivery_context:
            raise AssertionError("delivery context is required")
        if self.host is None or self.host.saved < 1:
            raise AssertionError("afterglow must be saved before acknowledgement")
        self.acked.extend(refs)
        return {"acked": len(refs)}


class Host(MemoryCompanionAdapterMixin):
    enable_memory_companion_emotional_drift = True
    enable_memory_companion_cross_window_emotion = True

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        bridge.host = self
        self.data = {"state_conditions": [], "daily_weather": {}}
        self.saved = 0

    def _memory_companion_bridge(self):
        return self.bridge

    @staticmethod
    def _safe_event_is_private(event: Event) -> bool:
        return event.is_private_chat()

    @staticmethod
    def _safe_event_sender_id(event: Event) -> str:
        return event.get_sender_id()

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return user_id

    @staticmethod
    def _memory_companion_bridge_bot_id(_event: Event | None = None) -> str:
        return "bot-1"

    def _compose_state_from_conditions(self, _weather: dict[str, Any]) -> dict[str, Any]:
        return {"energy": 79, "conditions": list(self.data["state_conditions"])}

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class EmotionE4AfterglowConditionTests(unittest.IsolatedAsyncioTestCase):
    async def test_afterglow_upserts_by_event_id_and_acks_after_save(self) -> None:
        bridge = Bridge()
        host = Host(bridge)
        event = Event()
        user = {"umo": event.unified_msg_origin}

        await host._memory_companion_apply_emotional_drift(event=event, user_id="user-1", user=user)

        self.assertIs(bridge.registered, host)
        self.assertEqual(1, host.saved)
        self.assertEqual(1, len(host.data["state_conditions"]))
        condition = host.data["state_conditions"][0]
        self.assertEqual("memory_afterglow", condition["kind"])
        self.assertEqual("emo-a", condition["source_event_id"])
        self.assertEqual(1800.0, condition["half_life_seconds"])
        self.assertEqual([{"event_id": "emo-a", "revision": 1}], bridge.acked)
        self.assertEqual(
            {
                "bot_id": "bot-1",
                "scope": "private",
                "platform": "qq",
                "user_id": "user-1",
                "session_id": "qq:FriendMessage:user-1",
                "allow_cross_window": True,
            },
            bridge.delivery_calls[0],
        )

    async def test_afterglow_delivery_fails_closed_without_matching_private_identity(self) -> None:
        bridge = Bridge()
        host = Host(bridge)
        event = Event(private=False)

        await host._memory_companion_apply_emotional_drift(
            event=event,
            user_id="user-1",
            user={"umo": event.unified_msg_origin},
        )
        await host._memory_companion_apply_emotional_drift(
            event=Event(),
            user_id="user-2",
            user={"umo": "qq:FriendMessage:user-1"},
        )

        self.assertEqual([], bridge.delivery_calls)
        self.assertEqual([], bridge.list_calls)
        self.assertEqual([], bridge.acked)
        self.assertEqual(0, host.saved)

    def test_afterglow_decay_is_monotonic(self) -> None:
        source = (ROOT / "daily_state.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DailyStateMixin")
        methods = {
            node.name: node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"_memory_afterglow_decay", "_condition_effective_energy_delta"}
        }
        module = ast.Module(body=list(methods.values()), type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "Any": Any,
            "_safe_float": lambda value, default=0.0: float(value or default),
            "_safe_int": lambda value, default=0, *_args: int(float(value or default)),
        }
        exec(compile(module, str(ROOT / "daily_state.py"), "exec"), namespace)
        host = SimpleNamespace()
        host._memory_afterglow_decay = lambda cond, now: namespace["_memory_afterglow_decay"](cond, now=now)
        condition = {"kind": "memory_afterglow", "energy_delta": -8, "start_ts": 1000.0, "half_life_seconds": 100.0}
        values = [namespace["_condition_effective_energy_delta"](host, condition, now=at) for at in (1000.0, 1100.0, 1200.0)]
        self.assertEqual([-8, -4, -2], values)

    def test_afterglow_is_only_requested_from_the_private_message_path(self) -> None:
        private_pipeline = (ROOT / "message_pipeline.py").read_text(encoding="utf-8")
        proactive = (ROOT / "proactive_message.py").read_text(encoding="utf-8")
        self.assertIn("await self._memory_companion_apply_emotional_drift(", private_pipeline)
        self.assertIn("user=fast_user", private_pipeline)
        self.assertNotIn("_memory_companion_apply_emotional_drift", proactive)


if __name__ == "__main__":
    unittest.main()
