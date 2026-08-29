from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch


class _AstrBotStub:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass


def _astrbot_stubs() -> dict[str, types.ModuleType]:
    names = (
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.message_components",
        "astrbot.api.provider",
        "astrbot.api.star",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.message",
        "astrbot.core.astr_main_agent",
        "astrbot.core.db",
        "astrbot.core.db.po",
        "astrbot.core.message",
        "astrbot.core.message.components",
        "astrbot.core.platform",
        "astrbot.core.platform.astrbot_message",
        "astrbot.core.platform.message_session",
        "astrbot.core.platform.message_type",
        "astrbot.core.platform.platform",
        "astrbot.core.platform.platform_metadata",
        "astrbot.core.provider",
        "astrbot.core.provider.entities",
        "astrbot.core.star",
        "astrbot.core.star.star",
        "astrbot.core.star.star_handler",
        "astrbot.core.utils",
        "astrbot.core.utils.astrbot_path",
    )
    modules = {name: types.ModuleType(name) for name in names}
    for name, module in modules.items():
        if any(other.startswith(f"{name}.") for other in names):
            module.__path__ = []
        module.__getattr__ = lambda _name: _AstrBotStub
    for name, module in modules.items():
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(modules[parent], child, module)
    modules["astrbot.api"].logger = types.SimpleNamespace(
        debug=lambda *_args, **_kwargs: None,
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
    )
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api.event"].AstrMessageEvent = _AstrBotStub
    modules["astrbot.api.event"].MessageChain = _AstrBotStub
    modules["astrbot.api.event"].filter = _AstrBotStub
    modules["astrbot.core.star.star"].star_map = {}
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: (
        tempfile.gettempdir()
    )
    return modules


_root = Path(__file__).resolve().parents[1]
_package = types.ModuleType("astrbot_plugin_private_companion")
_package.__path__ = [str(_root)]
_package.__package__ = "astrbot_plugin_private_companion"
sys.modules.setdefault("astrbot_plugin_private_companion", _package)
try:
    from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
except ImportError:
    # Keep the fallback local to this import.  Updating sys.modules globally
    # poisons later full-suite collection when the real AstrBot is installed.
    with patch.dict(sys.modules, _astrbot_stubs()):
        from astrbot_plugin_private_companion.proactive_message import (
            ProactiveMessageMixin,
        )


class _ScenePromptHarness(ProactiveMessageMixin):
    def __init__(self, response: Any = "", *, raises: bool = False) -> None:
        self.data = {"daily_state": {}, "daily_plan": {}}
        self.photo_generation_style = "真实"
        self.photo_generation_style_custom_prompt = ""
        self.photo_generation_prompt_format = "traditional"
        self.photo_prompt_provider_id = ""
        self.mai_style_provider_id = ""
        self.enable_bot_relationship_network = False
        self._get_current_plan_item = lambda *_args: {}
        self._format_state_for_prompt = lambda *_args, **_kwargs: ""
        self._format_content_choice_options_for_prompt = lambda *_args: (
            "objects, scenery, food"
        )
        self._deferred_immediate_share_tense_hint = lambda *_args, **_kwargs: False
        self._get_default_persona_prompt = lambda: "stable companion persona"
        self._format_plan_item_for_prompt = lambda *_args: "at home"
        if raises:

            async def fail(*_args: Any, **_kwargs: Any) -> str:
                raise TimeoutError("photo prompt timeout")

            self._llm_call = fail
        else:
            self._llm_call = AsyncMock(return_value=response)

    def _task_provider(self, *_args: Any) -> str:
        return ""

    @staticmethod
    def _extract_json_payload(raw: str) -> Any:
        try:
            return json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


class _ActionHarness(ProactiveMessageMixin):
    def __init__(self, *, reference_path: str = "", selected_path: str = "") -> None:
        self.enable_photo_text_action = True
        self.enable_photo_reference_image = bool(reference_path)
        self._data_lock = asyncio.Lock()
        self.reference_path = reference_path
        self.selected_path = selected_path
        self.generate_calls: list[dict[str, Any]] = []

    def _photo_generation_scope_allowed(self, **_kwargs: Any) -> bool:
        return True

    def _photo_text_load_defer_note(self, *_args: Any, **_kwargs: Any) -> str:
        return ""

    def _photo_text_available(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def _build_photo_scene_prompt(
        self, *_args: Any, **_kwargs: Any
    ) -> dict[str, Any]:
        return {
            "kind": "text2img",
            "prompt": "single character reading beside a sunny window",
            "caption": "A quiet reading moment shared with you.",
            "subject_owner": "bot",
            "use_persona_reference": True,
        }

    async def _photo_persona_reference_image_for_kind_async(
        self, *_args: Any, **_kwargs: Any
    ) -> str:
        return self.selected_path

    def _photo_persona_reference_image_for_kind(
        self, *_args: Any, **_kwargs: Any
    ) -> str:
        return self.reference_path

    async def _photo_persona_reference_image_path_async(self) -> str:
        return ""

    async def _generate_photo_image(self, **kwargs: Any) -> tuple[str, str, str]:
        self.generate_calls.append(dict(kwargs))
        return "mock-backend", "generated.png", "ok"

    def _note_photo_generation_attempt(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def _save_data_sync(self, **_kwargs) -> None:
        return None


def test_photo_prompt_timeout_keeps_topic_motive_and_english_style() -> None:
    async def run() -> dict[str, Any]:
        harness = _ScenePromptHarness(raises=True)
        return await harness._build_photo_scene_prompt(
            {
                "planned_proactive_topic": "a small bookstore corner after rain",
                "planned_proactive_motive": "want to share its quiet warmth",
            },
            "friend",
            "quiet_care",
        )

    scene = asyncio.run(run())

    assert "a small bookstore corner after rain" in scene["prompt"]
    assert "want to share its quiet warmth" in scene["prompt"]
    assert "realistic photography style" in scene["prompt"]
    assert "a small bookstore corner after rain" in scene["caption"]
    assert scene["use_persona_reference"] is True
    assert scene["subject_owner"] == "bot"


def test_photo_prompt_empty_response_keeps_specific_topic() -> None:
    async def run() -> dict[str, Any]:
        harness = _ScenePromptHarness("")
        return await harness._build_photo_scene_prompt(
            {
                "planned_proactive_topic": "silver ginkgo leaves after rain",
                "planned_proactive_motive": "want to share the quiet colors",
            },
            "friend",
            "state_share",
        )

    scene = asyncio.run(run())

    assert "silver ginkgo leaves after rain" in scene["prompt"]
    assert "want to share the quiet colors" in scene["prompt"]
    assert "silver ginkgo leaves after rain" in scene["caption"]
    assert (
        "Casual everyday snapshot with a concrete subject from the current context"
        not in scene["prompt"]
    )


def test_photo_prompt_invalid_json_keeps_specific_topic() -> None:
    async def run() -> dict[str, Any]:
        harness = _ScenePromptHarness("not valid json")
        return await harness._build_photo_scene_prompt(
            {
                "planned_proactive_topic": "a blue mug beside the notebook",
                "planned_proactive_motive": "want to share a calm study moment",
            },
            "friend",
            "state_share",
        )

    scene = asyncio.run(run())

    assert "a blue mug beside the notebook" in scene["prompt"]
    assert "want to share a calm study moment" in scene["prompt"]
    assert "a blue mug beside the notebook" in scene["caption"]


def test_model_food_scene_without_reference_flag_stays_pure_scene() -> None:
    async def run() -> dict[str, Any]:
        harness = _ScenePromptHarness(
            json.dumps(
                {
                    "kind": "text2img",
                    "prompt": "A bowl of tomato soup on a wooden table, realistic photography style",
                    "caption": "A warm bowl of soup.",
                }
            )
        )
        return await harness._build_photo_scene_prompt({}, "friend", "state_share")

    scene = asyncio.run(run())

    assert scene["use_persona_reference"] is False
    assert scene["subject_owner"] == "scene"
    assert "do not insert an unrequested person" in scene["prompt"]


def test_selector_zero_uses_identity_fallback_before_backend_submission(
    tmp_path: Path,
) -> None:
    persona = tmp_path / "persona.png"
    persona.write_bytes(b"placeholder image")

    async def run() -> tuple[str, list[dict[str, Any]]]:
        harness = _ActionHarness(reference_path=str(persona))
        result = await harness._run_photo_text_action(
            {"user_id": "10001", "umo": "private:10001"},
            "friend",
            "quiet_care",
        )
        return result, harness.generate_calls

    result, calls = asyncio.run(run())

    assert calls
    assert calls[0]["reference_image_path"] == str(persona.resolve())
    assert "identity_fallback" in result
    assert "人物参考图：已使用" in result


def test_pure_scene_keeps_reference_empty_and_adds_no_person_constraint() -> None:
    class PureSceneHarness(_ActionHarness):
        async def _build_photo_scene_prompt(
            self, *_args: Any, **_kwargs: Any
        ) -> dict[str, Any]:
            return {
                "kind": "text2img",
                "prompt": "a bowl of tomato soup on a wooden table",
                "caption": "A bowl of tomato soup with steam rising.",
                "subject_owner": "scene",
                "use_persona_reference": False,
            }

        async def _photo_persona_reference_image_for_kind_async(
            self, *_args: Any, **_kwargs: Any
        ) -> str:
            raise AssertionError("pure scene must not select a persona reference")

        def _photo_persona_reference_image_for_kind(
            self, *_args: Any, **_kwargs: Any
        ) -> str:
            raise AssertionError("pure scene must not resolve a persona reference")

    async def run() -> tuple[str, list[dict[str, Any]]]:
        harness = PureSceneHarness()
        result = await harness._run_photo_text_action(
            {"user_id": "10001", "umo": "private:10001"},
            "friend",
            "state_share",
        )
        return result, harness.generate_calls

    result, calls = asyncio.run(run())

    assert calls
    assert calls[0]["reference_image_path"] == ""
    assert "people" in calls[0]["prompt_text"]
    assert "human figures" in calls[0]["prompt_text"]
    assert "人物参考图：未使用" in result


def test_missing_identity_reference_stops_before_backend_submission() -> None:
    async def run() -> tuple[str, list[dict[str, Any]]]:
        harness = _ActionHarness()
        result = await harness._run_photo_text_action(
            {"user_id": "10001", "umo": "private:10001"},
            "friend",
            "quiet_care",
        )
        return result, harness.generate_calls

    result, calls = asyncio.run(run())

    assert calls == []
    assert "缺少有效身份参考图" in result
    assert "不能生成无来源的人脸" in result


def test_third_party_does_not_inherit_bot_reference(tmp_path: Path) -> None:
    persona = tmp_path / "persona.png"
    persona.write_bytes(b"placeholder image")

    class ThirdPartyHarness(_ActionHarness):
        async def _build_photo_scene_prompt(
            self, *_args: Any, **_kwargs: Any
        ) -> dict[str, Any]:
            return {
                "kind": "text2img",
                "prompt": "a third-party person standing by a window",
                "caption": "A person in the scene.",
                "subject_owner": "third_party",
                "use_persona_reference": True,
            }

    async def run() -> tuple[str, list[dict[str, Any]]]:
        harness = ThirdPartyHarness(reference_path=str(persona))
        result = await harness._run_photo_text_action(
            {"user_id": "10001", "umo": "private:10001"},
            "friend",
            "state_share",
        )
        return result, harness.generate_calls

    result, calls = asyncio.run(run())

    assert calls == []
    assert "不能套用 Bot 身份图" in result
