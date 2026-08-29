from __future__ import annotations

import importlib.util
import asyncio
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
_MEMORY_ROOT_CANDIDATES = (
    Path(os.environ["ASTRBOT_MEMORY_PLUGIN_ROOT"])
    if os.environ.get("ASTRBOT_MEMORY_PLUGIN_ROOT")
    else ROOT / ".missing-memory-root",
    ROOT.parent / "memory",
    ROOT.parent / "memory-official",
    ROOT.parents[1] / "astrbot_plugin_memory_companion-main",
    ROOT.parent / "astrbot_plugin_remember_you",
)
MEMORY_ROOT = next(
    (
        path
        for path in _MEMORY_ROOT_CANDIDATES
        if (path / "core" / "bridge.py").is_file()
    ),
    _MEMORY_ROOT_CANDIDATES[0],
)

try:
    import astrbot.api  # noqa: F401
except ImportError:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(warning=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None)
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api


def _load_companion_adapter():
    package_name = "c1_dual_companion"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.memory_companion_adapter",
        ROOT / "memory_companion_adapter.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoryCompanionAdapterMixin


def _load_memory_bridge():
    package = types.ModuleType("c1_dual_memory")
    package.__path__ = [str(MEMORY_ROOT)]
    sys.modules[package.__name__] = package
    core = types.ModuleType("c1_dual_memory.core")
    core.__path__ = [str(MEMORY_ROOT / "core")]
    sys.modules[core.__name__] = core
    spec = importlib.util.spec_from_file_location(
        "c1_dual_memory.core.bridge",
        MEMORY_ROOT / "core" / "bridge.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoryCompanionBridge


MemoryCompanionAdapterMixin = _load_companion_adapter()
MemoryCompanionBridge = _load_memory_bridge()


class _Companion(MemoryCompanionAdapterMixin):
    def __init__(self, bridge, *, users=None):
        self.enable_memory_companion_bridge = True
        self.bridge = bridge
        self.data = {
            "users": users
            if isinstance(users, dict)
            else {
                "u1": {
                    "user_id": "u1",
                    "enabled": True,
                    "private_memory_enabled": True,
                    "umo": "default:FriendMessage:u1",
                    "identity_bot_id": "bot-1",
                }
            }
        }
        self.bot_self_id = "bot-1"

    def _memory_companion_bridge_uncached(self):
        return self.bridge


def test_chat_companion_accepts_chat_memory_capability_contract():
    bridge = MemoryCompanionBridge(
        SimpleNamespace(companion_coordination_status=lambda: {"available": True, "state": "ready"})
    )
    companion = _Companion(bridge)

    assert companion._memory_companion_bridge() is bridge
    probe = bridge.probe_bot_personal_memory_capabilities()
    assert companion._bridge_last_status["contract_fingerprint"] == probe["contract_fingerprint"]
    status = companion._memory_companion_coordination_status()
    assert status["available"] is True
    assert status["state"] == "ready"
    assert probe["available"] is True


def test_chat_companion_projects_redacted_user_memory_summary():
    class _MemoryService:
        @staticmethod
        async def read_user_memory_summary(user_id, *, session_id="", limit=6):
            return {
                "contract": "memory.user_memory_summary.v1",
                "ok": True,
                "state": "ready",
                "user_id": user_id,
                "session_id": session_id,
                "counts": {
                    "profile": 2,
                    "preference": 3,
                    "relationship": 4,
                    "private_conversation": 5,
                },
                "summaries": [
                    {"category": "profile", "summary": "用户画像记忆（内容已脱敏）"},
                    {"category": "private_conversation", "summary": "私聊连续性记忆（内容已脱敏）"},
                ],
            }

    service = _MemoryService()
    companion = _Companion(None)
    companion.bridge = MemoryCompanionBridge(service)
    service.context = SimpleNamespace(
        get_all_stars=lambda: [
            SimpleNamespace(
                star_cls=companion,
                root_dir_name="astrbot_plugin_private_companion",
                name="astrbot_plugin_private_companion",
                activated=True,
            )
        ]
    )
    result = asyncio.run(companion._memory_companion_read_user_memory_summary("u1", limit=3))

    assert result == {
        "schema_version": "memory.user_memory_summary.v1",
        "available": True,
        "state": "ready",
        "counts": {"profile": 2, "preference": 3, "relationship": 4, "private_chat": 5},
        "summaries": {
            "profile": "用户画像记忆（内容已脱敏）",
            "private_chat": "私聊连续性记忆（内容已脱敏）",
        },
        "workspace_path": "",
    }


def test_user_memory_summary_supplies_scoped_requester_context_when_supported():
    requester_context = object()

    class _ProtectedBridge:
        @staticmethod
        def create_user_memory_context(capability, **kwargs):
            assert capability == "capability"
            assert kwargs == {
                "bot_id": "bot-1",
                "scope": "private",
                "platform": "default",
                "user_id": "u1",
                "session_id": "default:FriendMessage:u1",
            }
            return requester_context

        @staticmethod
        async def read_user_memory_summary(user_id, *, session_id="", limit=6, requester_context=None):
            assert user_id == "u1"
            assert session_id == "default:FriendMessage:u1"
            assert requester_context is not None
            return {
                "contract": "memory.user_memory_summary.v1",
                "ok": True,
                "state": "ready",
                "counts": {"profile": 1},
                "summaries": [{"category": "profile", "summary": "已授权摘要"}],
            }

    class _ProtectedCompanion(_Companion):
        def _memory_companion_bridge(self):
            return self.bridge

        @staticmethod
        def _memory_companion_bridge_bot_id(_event=None):
            return ""

        @staticmethod
        def _memory_companion_emotion_producer_capability(_bridge):
            return "capability"

    companion = _ProtectedCompanion(_ProtectedBridge())
    result = asyncio.run(
        companion._memory_companion_read_user_memory_summary(
            "u1",
            session_id="default:FriendMessage:u1",
        )
    )

    assert result["available"] is True
    assert result["summaries"]["profile"] == "已授权摘要"


def test_chat_companion_user_summary_degrades_without_memory_reader():
    companion = _Companion(MemoryCompanionBridge(SimpleNamespace()))
    result = asyncio.run(companion._memory_companion_read_user_memory_summary("u1"))
    assert result["available"] is False
    assert result["reason_code"] == "requester_capability_unavailable"


def test_user_memory_summary_rejects_group_and_untrusted_identities_before_bridge_access():
    class _MemoryService:
        calls = 0

        async def read_user_memory_summary(self, *_args, **_kwargs):
            self.calls += 1
            return {"contract": "memory.user_memory_summary.v1", "ok": True, "state": "ready"}

    service = _MemoryService()
    companion = _Companion(
        MemoryCompanionBridge(service),
        users={
            "group-user": {"user_id": "group-user", "observation_only": True, "enabled": True},
            "opted-out": {"user_id": "opted-out", "enabled": True, "private_memory_enabled": False},
        },
    )

    group_result = asyncio.run(companion._memory_companion_read_user_memory_summary("group-user"))
    disabled_result = asyncio.run(companion._memory_companion_read_user_memory_summary("opted-out"))
    unknown_result = asyncio.run(companion._memory_companion_read_user_memory_summary("unknown"))

    assert group_result["reason_code"] == "group_observation_forbidden"
    assert disabled_result["reason_code"] == "private_memory_disabled"
    assert unknown_result["reason_code"] == "private_identity_untrusted"
    assert service.calls == 0
