from __future__ import annotations

import inspect
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class _AstrBotStub:
    def __init__(self, *_args, **_kwargs):
        pass


def _astrbot_stubs() -> dict[str, types.ModuleType]:
    module_names = (
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
    modules = {name: types.ModuleType(name) for name in module_names}
    for name, module in modules.items():
        if any(other.startswith(f"{name}.") for other in module_names):
            module.__path__ = []
        module.__getattr__ = lambda _name: _AstrBotStub

    for name, module in modules.items():
        if "." in name:
            parent_name, child_name = name.rsplit(".", 1)
            setattr(modules[parent_name], child_name, module)

    api = modules["astrbot.api"]
    event = modules["astrbot.api.event"]
    api.logger = _Logger()
    api.AstrBotConfig = dict
    event.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event.MessageChain = _AstrBotStub
    event.filter = _AstrBotStub
    modules["astrbot.core.star.star"].star_map = {}
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: tempfile.gettempdir()
    return modules


with mock.patch.dict(sys.modules, _astrbot_stubs()):
    PLUGIN_ROOT = Path(__file__).resolve().parents[1]
    plugin_package = types.ModuleType("astrbot_plugin_private_companion")
    plugin_package.__path__ = [str(PLUGIN_ROOT)]
    plugin_package.__package__ = "astrbot_plugin_private_companion"
    sys.modules.setdefault("astrbot_plugin_private_companion", plugin_package)

    from astrbot_plugin_private_companion.photo_wardrobe_decision import (
        PhotoWardrobeDecision,
        analyze_photo_wardrobe,
    )
    from astrbot_plugin_private_companion.photo_reference_intent import analyze_reference_intent
    from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
    from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


def _candidate(
    reference_id: str,
    *,
    outfit_category: str,
    scene_categories: tuple[str, ...],
    note: str,
    time_categories: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "id": reference_id,
        "kind": "library",
        "path": f"C:/images/{reference_id}.png",
        "source": f"C:/images/{reference_id}.png",
        "note": note,
        "reference_roles": ["identity", "outfit", "scene"],
        "outfit_category": outfit_category,
        "outfit_lock_default": True,
        "scene_categories": list(scene_categories),
        "time_categories": list(time_categories),
        "preferred_preset": "",
        "metadata_source": "test",
    }


class _SelectionHarness(ProactiveMessageMixin):
    def __init__(
        self,
        candidates: list[dict[str, object]],
        llm_reply: str,
        *,
        persona_path: str = "",
    ):
        self.enable_photo_reference_image = True
        self._candidates = candidates
        self._llm_reply = llm_reply
        self._persona_path = persona_path
        self.llm_prompts: list[str] = []
        self.llm_kwargs: list[dict[str, object]] = []

    async def _photo_reference_candidates_async(
        self,
        *,
        allow_daily_outfit: bool = True,
        requester_user_id: str = "",
        request_text: str = "",
        ambient_context: str = "",
    ):
        return [dict(candidate) for candidate in self._candidates]

    @staticmethod
    def _recent_sent_photo_continuity_candidate(_continuity_key: str):
        return {}

    async def _llm_call(self, prompt: str, **_kwargs):
        self.llm_prompts.append(prompt)
        self.llm_kwargs.append(dict(_kwargs))
        return self._llm_reply

    async def _photo_persona_reference_image_path_async(self) -> str:
        return self._persona_path


class _ContinuityHarness(ProactiveMessageMixin):
    def __init__(self):
        self.data: dict[str, object] = {}
        self.saved = 0

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class _ToolPhotoHarness(LlmToolActionsMixin):
    natural_language_photo_generation_mode = "tool_first"
    enable_photo_text_action = True

    def __init__(self, image_path: str):
        self.image_path = image_path
        self.generation_kwargs: dict[str, object] = {}
        self.generation_calls = 0
        self.reference_fallback_message = ""
        self.delivered_caption = ""
        self.context_reference_images: list[tuple[str, str]] = []

    @staticmethod
    def _photo_text_available() -> bool:
        return True

    @staticmethod
    def _build_natural_language_photo_prompt(**kwargs) -> str:
        return str(kwargs.get("prompt") or "")

    async def _generate_photo_image_result(self, **kwargs):
        self.generation_calls += 1
        self.generation_kwargs = dict(kwargs)
        return types.SimpleNamespace(
            backend="test",
            image_path=self.image_path,
            note="",
            preset_names=("表情包场景",),
            preset_hint="",
            preset_source="workflow_default",
            suggestion_status="not_provided",
            reference_fallback_message=self.reference_fallback_message,
            as_legacy_tuple=lambda: ("test", self.image_path, ""),
        )

    async def _deliver_generated_image_to_event(self, _event, *, image_path: str, caption: str):
        self.delivered_caption = caption
        return {"sent": True, "destination": "test"}

    async def _photo_reference_images_from_command_context(self, _event, _user_id, *, limit=12):
        return self.context_reference_images[:limit], bool(self.context_reference_images)


class _ToolEvent:
    unified_msg_origin = "test-session"

    @staticmethod
    def get_sender_id() -> str:
        return "test-user"


class PhotoReferenceOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_trial_candidate_overrides_return_structured_model_selection(self) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        school = _candidate(
            "school-uniform",
            outfit_category="school_uniform",
            scene_categories=("school",),
            note="学校校服",
        )
        harness = _SelectionHarness([], llm_reply="1")

        result = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="晚上在卧室穿睡衣拍一张",
            candidate_overrides=[sleepwear, school],
            selection_provider_id="webui-main-provider",
            selection_strict_provider=True,
            return_selection_result=True,
        )

        self.assertEqual(result.selection_source, "model")
        self.assertTrue(result.model_attempted)
        self.assertEqual(result.model_selected_id, "sleepwear-bedroom")
        self.assertEqual(result.selected["id"], "sleepwear-bedroom")
        self.assertEqual(harness.llm_kwargs[0]["provider_id"], "webui-main-provider")
        self.assertIs(harness.llm_kwargs[0]["strict_provider"], True)

    async def test_text2img_trial_candidate_overrides_do_not_require_a_user_scope(self) -> None:
        harness = _SelectionHarness([], llm_reply="1")
        candidate = _candidate(
            "draft-identity",
            outfit_category="",
            scene_categories=(),
            note="未保存的人设草稿",
        )

        result = await harness._select_photo_reference_candidate_async(
            "text2img",
            request_text="生成一张人物照片",
            candidate_overrides=[candidate],
            selection_provider_id="webui-main-provider",
            selection_strict_provider=True,
            return_selection_result=True,
        )

        self.assertNotEqual(result.selection_reason, "workflow_does_not_use_reference")
        self.assertEqual(result.selected["id"], "draft-identity")

    async def test_llm_selfie_request_completes_generation_and_delivery(self) -> None:
        user_message = "来张自拍"
        llm_tool_call = {
            "prompt": "拍一张自拍",
            "kind": "selfie",
            "caption": "给你，刚拍的。",
            "send": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "generated.png"
            image_path.write_bytes(b"simulated-png")
            harness = _ToolPhotoHarness(str(image_path))

            payload = await harness._pc_generate_photo_impl(
                _ToolEvent(),
                **llm_tool_call,
            )

        result = __import__("json").loads(payload)
        self.assertEqual(user_message, "来张自拍")
        self.assertEqual(harness.generation_kwargs["request_text"], "拍一张自拍")
        self.assertEqual(harness.generation_kwargs["workflow_kind"], "selfie")
        self.assertEqual(harness.delivered_caption, "给你，刚拍的。")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["generated"])
        self.assertTrue(result["sent"])
        self.assertEqual(result["delivery"], "test")

    async def test_user_request_outweighs_conflicting_ambient_context(self) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        school = _candidate(
            "school-uniform",
            outfit_category="school_uniform",
            scene_categories=("school",),
            note="学校教室校服",
        )
        harness = _SelectionHarness([sleepwear, school], llm_reply="2")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="请在卧室穿睡衣拍一张照片",
            ambient_context="日程显示正在学校教室上学，身穿校服",
        )

        self.assertEqual(selected["id"], "sleepwear-bedroom")
        self.assertEqual(len(harness.llm_prompts), 1)
        self.assertIn("场景类别=bedroom,home", harness.llm_prompts[0])
        self.assertNotIn("preferred_preset", harness.llm_prompts[0])

    async def test_formal_selection_filters_guided_exclusions_and_disabled_candidates(self) -> None:
        blocked = _candidate(
            "blocked-school",
            outfit_category="",
            scene_categories=("school",),
            note="学校场景但明确禁用",
        )
        blocked.update(
            metadata_source="guided_editor",
            selection_eligibility="fallback_allowed",
            excluded_scene_categories=["school"],
        )
        disabled = _candidate(
            "disabled-school",
            outfit_category="",
            scene_categories=("school",),
            note="暂时停用",
        )
        disabled.update(
            metadata_source="guided_editor",
            selection_eligibility="disabled",
        )
        available = _candidate(
            "available-school",
            outfit_category="",
            scene_categories=("school",),
            note="学校可用",
        )
        available.update(
            metadata_source="guided_editor",
            selection_eligibility="matching_only",
        )
        harness = _SelectionHarness([blocked, disabled, available], llm_reply="1")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="在学校教室拍一张照片",
        )

        self.assertEqual(selected["id"], "available-school")
        self.assertEqual(len(harness.llm_prompts), 1)
        self.assertNotIn("blocked-school", harness.llm_prompts[0])
        self.assertNotIn("disabled-school", harness.llm_prompts[0])

    async def test_formal_policy_ignores_conflicting_ambient_and_historical_scenes(self) -> None:
        bedroom = _candidate(
            "bedroom-current",
            outfit_category="",
            scene_categories=("bedroom",),
            note="卧室场景",
        )
        bedroom.update(
            metadata_source="guided_editor",
            selection_eligibility="fallback_allowed",
            excluded_scene_categories=["school"],
        )
        harness = _SelectionHarness([bedroom], llm_reply="1")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="请在卧室拍一张照片",
            ambient_context="当前位置：学校教室",
            schedule_history_context="08:00-12:00｜已完成｜在学校上课",
        )

        self.assertEqual(selected["id"], "bedroom-current")

    async def test_formal_policy_treats_negated_scene_as_an_exclusion(self) -> None:
        bedroom = _candidate(
            "bedroom-current",
            outfit_category="",
            scene_categories=("bedroom",),
            note="卧室场景",
        )
        bedroom.update(
            metadata_source="guided_editor",
            selection_eligibility="matching_only",
            excluded_scene_categories=["school"],
        )
        school = _candidate(
            "school-blocked",
            outfit_category="",
            scene_categories=("school",),
            note="学校场景",
        )
        school.update(
            metadata_source="guided_editor",
            selection_eligibility="matching_only",
        )
        harness = _SelectionHarness([bedroom, school], llm_reply="1")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="不要在学校，在卧室拍一张",
        )

        self.assertEqual(selected["id"], "bedroom-current")
        self.assertNotIn("school-blocked", harness.llm_prompts[0])

    async def test_formal_matching_only_outfit_requires_an_outfit_match(self) -> None:
        sleepwear = _candidate(
            "sleepwear-only",
            outfit_category="sleepwear",
            scene_categories=(),
            note="睡衣参考",
        )
        sleepwear.update(
            metadata_source="guided_editor",
            selection_eligibility="matching_only",
        )
        harness = _SelectionHarness([sleepwear], llm_reply="1")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="请穿校服拍一张照片",
        )

        self.assertEqual(selected, {})

    def test_selection_interface_accepts_only_the_normalized_scene_suggestion(self) -> None:
        parameters = inspect.signature(
            ProactiveMessageMixin._select_photo_reference_candidate_async
        ).parameters

        self.assertNotIn("scene_preset", parameters)
        self.assertNotIn("requested_scene_preset", parameters)
        self.assertIn("suggested_scene_preset", parameters)

    async def test_model_choice_zero_returns_no_reference(self) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        school = _candidate(
            "school-uniform",
            outfit_category="school_uniform",
            scene_categories=("school",),
            note="学校教室校服",
        )
        harness = _SelectionHarness([sleepwear, school], llm_reply="0")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="请拍一张全新的照片",
            ambient_context="正在学校教室",
        )

        self.assertEqual(selected, {})

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                "随手拍一张",
                has_explicit_reference=True,
            ),
            wardrobe_intent=analyze_photo_wardrobe("随手拍一张"),
            request_text="随手拍一张",
            ambient_context="正在学校教室",
        )
        self.assertEqual(plan.bindings, ())
        self.assertEqual(plan.primary_reference_id, "")

    async def test_rejected_sleepwear_reference_falls_back_to_persona_identity(self) -> None:
        sleepwear = _candidate(
            "lace-sleepwear",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="蕾丝吊带睡衣",
        )
        harness = _SelectionHarness(
            [sleepwear],
            llm_reply="0",
            persona_path="C:/images/persona.png",
        )
        request = "穿保守长袖睡衣拍一张自拍"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.primary_reference_id, "persona")
        self.assertEqual(len(plan.bindings), 1)
        self.assertEqual(plan.bindings[0].path, "C:/images/persona.png")
        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertNotIn("outfit", plan.bindings[0].roles)

    async def test_matching_outfit_change_reference_restores_outfit_role(self) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        harness = _SelectionHarness([sleepwear], llm_reply="1")
        request = "换上睡衣"
        intent = analyze_reference_intent(request, workflow_kind="selfie")

        self.assertEqual(intent.requested_roles, ("identity",))
        self.assertEqual(intent.excluded_roles, ("outfit",))

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=intent,
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.primary_reference_id, "sleepwear-bedroom")
        self.assertEqual(plan.bindings[0].roles, ("identity", "outfit"))
        self.assertEqual(plan.bindings[0].ignore, ("scene",))

    async def test_mismatched_outfit_change_keeps_selected_reference_identity_only(
        self,
    ) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        harness = _SelectionHarness([sleepwear], llm_reply="1")
        harness._select_photo_reference_candidate_async = mock.AsyncMock(
            return_value=dict(sleepwear)
        )
        request = "换上校服"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.primary_reference_id, "sleepwear-bedroom")
        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertIn("outfit", plan.bindings[0].ignore)
        self.assertEqual(plan.fallback_reason, "")

    async def test_explicit_reference_outfit_opt_out_wins_over_matching_category(
        self,
    ) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        harness = _SelectionHarness([sleepwear], llm_reply="1")
        request = "换上睡衣，但不要参考图里的衣服"
        wardrobe_intent = analyze_photo_wardrobe(request)

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=wardrobe_intent,
            request_text=request,
        )

        self.assertEqual(wardrobe_intent.target_category, "sleepwear")
        self.assertEqual(plan.primary_reference_id, "sleepwear-bedroom")
        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertIn("outfit", plan.bindings[0].ignore)

    async def test_matching_outfit_reference_does_not_require_default_lock(
        self,
    ) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        sleepwear["outfit_lock_default"] = False
        harness = _SelectionHarness([sleepwear], llm_reply="1")
        request = "换上睡衣"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.bindings[0].roles, ("identity", "outfit"))

    async def test_custom_outfit_does_not_restore_reference_outfit_role(self) -> None:
        custom = _candidate(
            "custom-outfit",
            outfit_category="custom_outfit",
            scene_categories=(),
            note="特殊定制服装",
        )
        harness = _SelectionHarness([custom], llm_reply="1")
        harness._select_photo_reference_candidate_async = mock.AsyncMock(
            return_value=dict(custom)
        )
        request = "换成红色吊带长裙"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertIn("outfit", plan.bindings[0].ignore)

    async def test_matching_category_without_outfit_responsibility_stays_identity_only(
        self,
    ) -> None:
        identity = _candidate(
            "identity-only",
            outfit_category="sleepwear",
            scene_categories=(),
            note="只用于身份",
        )
        identity["reference_roles"] = ["identity"]
        harness = _SelectionHarness([identity], llm_reply="1")
        harness._select_photo_reference_candidate_async = mock.AsyncMock(
            return_value=dict(identity)
        )
        request = "换上睡衣"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertNotIn("outfit", plan.bindings[0].roles)

    async def test_context_filled_outfit_category_does_not_restore_role(self) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        sleepwear["outfit_lock_default"] = False
        harness = _SelectionHarness([sleepwear], llm_reply="1")
        harness._select_photo_reference_candidate_async = mock.AsyncMock(
            return_value=dict(sleepwear)
        )

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                "随手拍一张",
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe("换上睡衣"),
            requested_outfit_category="",
            request_text="随手拍一张",
        )

        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertIn("outfit", plan.bindings[0].ignore)

    async def test_workflow_default_does_not_lock_mismatched_outfit_category(
        self,
    ) -> None:
        sleepwear = _candidate(
            "sleepwear-bedroom",
            outfit_category="sleepwear",
            scene_categories=("home", "bedroom"),
            note="卧室睡衣",
        )
        harness = _SelectionHarness([sleepwear], llm_reply="1")
        harness._select_photo_reference_candidate_async = mock.AsyncMock(
            return_value=dict(sleepwear)
        )
        request = "穿上校服"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                "随手拍一张",
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.bindings[0].roles, ("identity",))
        self.assertIn("outfit", plan.bindings[0].ignore)

    async def test_explicit_daily_outfit_request_keeps_daily_outfit_over_persona(
        self,
    ) -> None:
        daily_outfit = _candidate(
            "daily-outfit",
            outfit_category="daily_outfit",
            scene_categories=("outdoor",),
            note="今日穿搭，适合户外",
        )
        daily_outfit["kind"] = "daily_outfit"
        harness = _SelectionHarness(
            [daily_outfit],
            llm_reply="1",
            persona_path="C:/images/persona.png",
        )
        request = "在室外自拍，使用今日穿搭"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                workflow_kind="selfie",
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
        )

        self.assertEqual(plan.primary_reference_id, "daily-outfit")
        self.assertEqual(plan.bindings[0].path, "C:/images/daily-outfit.png")
        self.assertNotEqual(plan.bindings[0].reference_id, "persona")

    async def test_low_confidence_model_intent_stays_identity_only(self) -> None:
        harness = _SelectionHarness([], llm_reply=(
            '{"requested_roles":["identity","outfit","scene"],'
            '"excluded_roles":[],"continuity_mode":"continuation","confidence":0.42}'
        ))

        intent = await harness._analyze_photo_reference_intent_async(
            "照着这种感觉再画一张",
            workflow_kind="selfie",
            has_explicit_reference=True,
        )

        self.assertEqual(intent.requested_roles, ("identity",))
        self.assertEqual(intent.excluded_roles, ())
        self.assertEqual(intent.continuity_mode, "ambiguous")
        self.assertLess(intent.confidence, 0.7)
        self.assertEqual(intent.source, "model_conservative")

    async def test_reference_models_use_active_persona_provider_settings(self) -> None:
        harness = _SelectionHarness(
            [
                _candidate("one", outfit_category="", scene_categories=(), note="第一张"),
                _candidate("two", outfit_category="", scene_categories=(), note="第二张"),
            ],
            llm_reply=(
                '{"requested_roles":["identity"],"excluded_roles":[],'
                '"continuity_mode":"continuation","confidence":0.9}'
            ),
        )
        harness.provider_config_mode = "precision"
        harness.photo_prompt_provider_id = "primary-photo"
        harness.fast_response_provider_id = "primary-fast"
        harness.llm_provider_id = "primary-llm"
        harness.mai_style_provider_id = "primary-style"
        persona_values = {
            "PHOTO_PROMPT_PROVIDER_ID": "persona-photo",
            "FAST_RESPONSE_PROVIDER_ID": "persona-fast",
            "LLM_PROVIDER_ID": "persona-llm",
            "MAI_STYLE_PROVIDER_ID": "persona-style",
        }
        harness.persona_setting = lambda key, default=None: persona_values.get(key, default)
        harness._task_provider = lambda *values: next(
            (value for value in values if str(value or "").strip()),
            "",
        )

        await harness._analyze_photo_reference_intent_async(
            "照着这种感觉再画一张",
            workflow_kind="selfie",
            has_explicit_reference=True,
        )
        self.assertEqual("persona-photo", harness.llm_kwargs[-1]["provider_id"])

        harness._llm_reply = "1"
        await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="从这两张里选一个继续画",
        )
        self.assertEqual("persona-photo", harness.llm_kwargs[-1]["provider_id"])

    async def test_generic_reference_request_skips_extra_intent_model_call(self) -> None:
        harness = _SelectionHarness([], llm_reply=(
            '{"requested_roles":["identity","outfit"],'
            '"excluded_roles":[],"continuity_mode":"ambiguous","confidence":0.9}'
        ))

        intent = await harness._analyze_photo_reference_intent_async(
            "参考一下",
            workflow_kind="selfie",
            has_explicit_reference=True,
        )

        self.assertEqual(intent.requested_roles, ("identity",))
        self.assertEqual(intent.source, "conservative")
        self.assertEqual(harness.llm_prompts, [])

    async def test_reference_selection_ignores_recent_sent_photo_state(self) -> None:
        persona = _candidate(
            "persona",
            outfit_category="",
            scene_categories=(),
            note="基础身份图",
        )
        harness = _SelectionHarness([persona], llm_reply="1")
        harness._recent_sent_photo_continuity_candidate = lambda _key: {
            "id": "recent_sent_photo",
            "kind": "recent_sent_photo",
            "path": "C:/images/recent.png",
            "source": "C:/images/recent.png",
            "reference_roles": ["identity", "outfit", "scene", "continuity"],
            "outfit_lock_default": True,
        }

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="重新拍一张",
            continuity_key="legacy-session-key",
        )

        self.assertEqual(selected["id"], "persona")
        self.assertNotIn("recent_sent_photo", "\n".join(harness.llm_prompts))

    async def test_continuation_plan_uses_normal_selection_without_explicit_reference(self) -> None:
        persona = _candidate(
            "persona",
            outfit_category="",
            scene_categories=(),
            note="基础身份图",
        )
        harness = _SelectionHarness([persona], llm_reply="1")
        recent = {
            "id": "recent_sent_photo",
            "kind": "recent_sent_photo",
            "path": "C:/images/recent.png",
            "source": "C:/images/recent.png",
            "reference_roles": ["identity", "outfit", "scene", "continuity"],
            "outfit_category": "school_uniform",
            "outfit_lock_default": True,
        }
        harness._recent_sent_photo_continuity_candidate = lambda _key: dict(recent)
        intent = analyze_reference_intent("接着上一张但换成睡衣")

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=intent,
            wardrobe_intent=analyze_photo_wardrobe("接着上一张但换成睡衣"),
            request_text="接着上一张但换成睡衣",
            continuity_key="session",
        )

        self.assertEqual(plan.primary_reference_id, "persona")
        self.assertEqual(
            plan.bindings[0].roles,
            ("identity", "scene"),
        )
        self.assertEqual(plan.bindings[0].ignore, ("outfit",))

    async def test_explicit_image_keeps_priority_over_stored_continuity_state(self) -> None:
        explicit = _candidate(
            "explicit",
            outfit_category="",
            scene_categories=(),
            note="用户本轮图片",
        )
        explicit["reference_roles"] = ["identity"]
        harness = _SelectionHarness([explicit], llm_reply="1")
        harness._recent_sent_photo_continuity_candidate = lambda _key: {
            "id": "recent_sent_photo",
            "kind": "recent_sent_photo",
            "path": "C:/images/recent.png",
            "source": "C:/images/recent.png",
            "reference_roles": ["identity", "outfit", "scene", "continuity"],
            "outfit_lock_default": True,
        }

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent("接着上一张", has_explicit_reference=True),
            wardrobe_intent=analyze_photo_wardrobe("接着上一张"),
            request_text="接着上一张",
            continuity_key="session",
            explicit_reference_paths=[explicit["path"]],
        )

        self.assertEqual(plan.primary_reference_id, "explicit_reference")
        self.assertEqual(len(plan.bindings), 1)
        self.assertNotEqual(plan.bindings[0].path, "C:/images/recent.png")

    async def test_unclassified_explicit_image_is_resolved_without_image_mixin(self) -> None:
        harness = _SelectionHarness([], llm_reply="1")

        candidate = await harness._photo_reference_candidate_for_path_async(
            "C:/images/current-request.png",
            workflow_kind="selfie",
        )

        self.assertEqual(candidate["kind"], "explicit")
        self.assertEqual(candidate["reference_roles"], ["identity"])
        self.assertEqual(candidate["metadata_source"], "runtime")

    async def test_explicit_matching_outfit_metadata_restores_outfit_role(self) -> None:
        explicit = _candidate(
            "explicit-sleepwear",
            outfit_category="sleepwear",
            scene_categories=("home",),
            note="睡衣参考图",
        )
        harness = _SelectionHarness([explicit], llm_reply="1")
        request = "换上睡衣"

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=analyze_reference_intent(
                request,
                has_explicit_reference=True,
            ),
            wardrobe_intent=analyze_photo_wardrobe(request),
            request_text=request,
            explicit_reference_paths=[explicit["path"]],
        )

        self.assertEqual(plan.primary_reference_id, "explicit_reference")
        self.assertEqual(plan.bindings[0].roles, ("identity", "outfit"))

    async def test_user_assigned_role_overrides_explicit_image_default_metadata(
        self,
    ) -> None:
        candidate = _candidate(
            "explicit",
            outfit_category="",
            scene_categories=(),
            note="默认身份图",
        )
        candidate["reference_roles"] = ["identity"]
        harness = _SelectionHarness([candidate], llm_reply="1")
        intent = analyze_reference_intent(
            "只参考这套衣服",
            has_explicit_reference=True,
        )

        plan = await harness._select_photo_reference_plan_async(
            "selfie",
            reference_intent=intent,
            wardrobe_intent=analyze_photo_wardrobe("只参考这套衣服"),
            request_text="只参考这套衣服",
            explicit_reference_paths=[candidate["path"]],
        )

        self.assertEqual(plan.bindings[0].roles, ("outfit",))
        self.assertEqual(plan.bindings[0].ignore, ("identity",))
        self.assertEqual(plan.primary_reference_id, "explicit_reference")

    async def test_identity_only_candidate_is_not_filtered_by_outfit_label(self) -> None:
        identity = _candidate(
            "identity-only",
            outfit_category="sleepwear",
            scene_categories=(),
            note="基础身份参考",
        )
        identity["reference_roles"] = ["identity"]
        identity["outfit_lock_default"] = False
        harness = _SelectionHarness([identity], llm_reply="1")

        selected = await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="穿校服拍一张照片",
            ambient_context="",
        )

        self.assertEqual(selected["id"], "identity-only")

    async def test_sticker_default_is_not_recorded_as_tool_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "sticker.png"
            image_path.write_bytes(b"png")
            harness = _ToolPhotoHarness(str(image_path))

            payload = await harness._pc_generate_photo_impl(
                _ToolEvent(),
                prompt="开心地挥挥手",
                kind="sticker",
                send=False,
            )

        result = __import__("json").loads(payload)
        self.assertEqual(harness.generation_kwargs["requested_scene_preset"], "")
        self.assertEqual(
            harness.generation_kwargs["workflow_default_scene_preset"],
            "表情包场景",
        )
        self.assertEqual(result["preset_hint"], "")
        self.assertEqual(result["preset_source"], "workflow_default")
        self.assertEqual(result["suggestion_status"], "not_provided")
        self.assertEqual(result["final_presets"], ["表情包场景"])

    async def test_tool_passes_multiple_reference_paths_to_generation_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.png"
            first = root / "face.png"
            second = root / "outfit.png"
            for path in (output, first, second):
                path.write_bytes(b"png")
            harness = _ToolPhotoHarness(str(output))

            async def resolve(source, **_kwargs):
                return str(source) if source in {str(first), str(second)} else ""

            harness._photo_reference_source_to_stable_path = resolve

            payload = await harness._pc_generate_photo_impl(
                _ToolEvent(),
                prompt="用第一张的脸，第二张的衣服",
                kind="selfie",
                reference_image_paths=[str(first), str(second)],
                send=False,
            )

        result = __import__("json").loads(payload)
        self.assertEqual(result["status"], "success")
        self.assertEqual(harness.generation_calls, 1)
        self.assertEqual(harness.generation_kwargs["reference_image_path"], str(first))
        self.assertEqual(
            harness.generation_kwargs["reference_image_paths"],
            [str(first), str(second)],
        )

    async def test_tool_rejects_all_references_when_one_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.png"
            missing = root / "missing-face.png"
            outfit = root / "outfit.png"
            output.write_bytes(b"png")
            outfit.write_bytes(b"png")
            harness = _ToolPhotoHarness(str(output))

            async def resolve(source, **_kwargs):
                return str(outfit) if source == str(outfit) else ""

            harness._photo_reference_source_to_stable_path = resolve

            payload = await harness._pc_generate_photo_impl(
                _ToolEvent(),
                prompt="用第一张的脸，第二张的衣服",
                kind="selfie",
                reference_image_paths=[str(missing), str(outfit)],
                send=False,
            )

        result = __import__("json").loads(payload)
        self.assertEqual(result["status"], "invalid_reference")
        self.assertFalse(result["success"])
        self.assertFalse(result["generated"])
        self.assertFalse(result["sent"])
        self.assertTrue(result["must_not_claim_sent"])
        self.assertFalse(result["retryable"])
        self.assertEqual(harness.generation_calls, 0)
        self.assertEqual(harness.generation_kwargs, {})
        self.assertNotIn(str(missing), payload)
        self.assertNotIn(str(outfit), payload)

    async def test_tool_rejects_all_references_when_one_resolver_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.png"
            broken = root / "broken-face.png"
            outfit = root / "outfit.png"
            output.write_bytes(b"png")
            outfit.write_bytes(b"png")
            harness = _ToolPhotoHarness(str(output))

            async def resolve(source, **_kwargs):
                if source == str(broken):
                    raise OSError("unreadable reference")
                return source

            harness._photo_reference_source_to_stable_path = resolve
            payload = await harness._pc_generate_photo_impl(
                _ToolEvent(),
                prompt="用第一张的脸，第二张的衣服",
                kind="selfie",
                reference_image_paths=[str(broken), str(outfit)],
                send=False,
            )

        result = __import__("json").loads(payload)
        self.assertEqual(result["status"], "invalid_reference")
        self.assertFalse(result["success"])
        self.assertFalse(result["generated"])
        self.assertFalse(result["sent"])
        self.assertTrue(result["must_not_claim_sent"])
        self.assertFalse(result["retryable"])
        self.assertEqual(harness.generation_calls, 0)
        self.assertEqual(harness.generation_kwargs, {})
        self.assertNotIn(str(broken), payload)
        self.assertNotIn(str(outfit), payload)

    async def test_tool_collects_multiple_context_images_for_indexed_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "output.png"
            face = root / "face.png"
            outfit = root / "outfit.png"
            for path in (output, face, outfit):
                path.write_bytes(b"png")
            harness = _ToolPhotoHarness(str(output))
            harness.context_reference_images = [
                (str(face), "随消息发送的图片"),
                (str(outfit), "随消息发送的图片"),
            ]

            await harness._pc_generate_photo_impl(
                _ToolEvent(),
                prompt="用第一张的脸，第二张的衣服",
                kind="selfie",
                send=False,
            )

        self.assertEqual(
            harness.generation_kwargs["reference_image_paths"],
            [str(face), str(outfit)],
        )

    async def test_reference_fallback_is_visible_in_delivered_caption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "output.png"
            output.write_bytes(b"png")
            harness = _ToolPhotoHarness(str(output))
            harness.reference_fallback_message = (
                "已保持人物身份，但没有找到匹配的服装参考图，本次服装按文字要求生成。"
            )

            await harness._pc_generate_photo_impl(
                _ToolEvent(),
                prompt="换成冬装",
                kind="selfie",
                send=True,
            )

        self.assertIn("已保持人物身份", harness.delivered_caption)
        self.assertIn("服装按文字要求生成", harness.delivered_caption)

    def test_user_scene_match_scores_above_ambient_scene_match(self) -> None:
        request_text = "在卧室拍一张照片"
        ambient_context = "日程显示正在学校教室"
        wardrobe_intent = analyze_photo_wardrobe(request_text)
        bedroom = _candidate(
            "bedroom",
            outfit_category="",
            scene_categories=("bedroom",),
            note="室内",
        )
        school = _candidate(
            "school",
            outfit_category="",
            scene_categories=("school",),
            note="室内",
        )

        bedroom_score = ProactiveMessageMixin._photo_reference_candidate_score(
            bedroom,
            request_text,
            ambient_context,
            wardrobe_intent=wardrobe_intent,
        )
        school_score = ProactiveMessageMixin._photo_reference_candidate_score(
            school,
            request_text,
            ambient_context,
            wardrobe_intent=wardrobe_intent,
        )

        self.assertGreater(bedroom_score, school_score)

    def test_structured_beach_scene_scores_above_unrelated_office_scene(self) -> None:
        request_text = "在海边拍一张自然照片"
        wardrobe_intent = analyze_photo_wardrobe(request_text)
        beach = _candidate(
            "beach",
            outfit_category="",
            scene_categories=("beach",),
            note="户外场景",
        )
        office = _candidate(
            "office",
            outfit_category="",
            scene_categories=("office",),
            note="室内场景",
        )

        beach_score = ProactiveMessageMixin._photo_reference_candidate_score(
            beach,
            request_text,
            "",
            wardrobe_intent=wardrobe_intent,
        )
        office_score = ProactiveMessageMixin._photo_reference_candidate_score(
            office,
            request_text,
            "",
            wardrobe_intent=wardrobe_intent,
        )

        self.assertGreater(beach_score, office_score)

    def test_schedule_history_weakly_affects_reference_score(self) -> None:
        request_text = "随手拍一张自拍"
        wardrobe_intent = analyze_photo_wardrobe(request_text)
        school = _candidate(
            "school",
            outfit_category="",
            scene_categories=("school",),
            note="学校教室",
        )
        bedroom = _candidate(
            "bedroom",
            outfit_category="",
            scene_categories=("bedroom",),
            note="卧室",
        )

        school_score = ProactiveMessageMixin._photo_reference_candidate_score(
            school,
            request_text,
            "",
            schedule_history_context="08:00-09:00｜已完成｜在学校教室上课",
            wardrobe_intent=wardrobe_intent,
        )
        bedroom_score = ProactiveMessageMixin._photo_reference_candidate_score(
            bedroom,
            request_text,
            "",
            schedule_history_context="08:00-09:00｜已完成｜在学校教室上课",
            wardrobe_intent=wardrobe_intent,
        )

        self.assertGreater(school_score, bedroom_score)

    def test_user_and_current_scene_each_outweigh_conflicting_history(self) -> None:
        bedroom = _candidate(
            "bedroom",
            outfit_category="",
            scene_categories=("bedroom",),
            note="卧室",
        )
        school = _candidate(
            "school",
            outfit_category="",
            scene_categories=("school",),
            note="学校教室",
        )
        history = "08:00-12:00｜已完成｜在学校教室上课"

        user_bedroom = ProactiveMessageMixin._photo_reference_candidate_score(
            bedroom,
            "请在卧室自拍",
            "",
            schedule_history_context=history,
            wardrobe_intent=analyze_photo_wardrobe("请在卧室自拍"),
        )
        user_school = ProactiveMessageMixin._photo_reference_candidate_score(
            school,
            "请在卧室自拍",
            "",
            schedule_history_context=history,
            wardrobe_intent=analyze_photo_wardrobe("请在卧室自拍"),
        )
        current_bedroom = ProactiveMessageMixin._photo_reference_candidate_score(
            bedroom,
            "随手自拍",
            "当前位置：卧室",
            schedule_history_context=history,
            wardrobe_intent=analyze_photo_wardrobe("随手自拍"),
        )
        current_school = ProactiveMessageMixin._photo_reference_candidate_score(
            school,
            "随手自拍",
            "当前位置：卧室",
            schedule_history_context=history,
            wardrobe_intent=analyze_photo_wardrobe("随手自拍"),
        )

        self.assertGreater(user_bedroom, user_school)
        self.assertGreater(current_bedroom, current_school)

    async def test_model_prompt_marks_history_as_non_current_context(self) -> None:
        harness = _SelectionHarness(
            [
                _candidate("school", outfit_category="", scene_categories=("school",), note="学校教室"),
                _candidate("bedroom", outfit_category="", scene_categories=("bedroom",), note="卧室"),
            ],
            llm_reply="1",
        )

        await harness._select_photo_reference_candidate_async(
            "selfie",
            request_text="随手自拍",
            ambient_context="当前位置：卧室",
            schedule_history_context="08:00-09:00｜已完成｜在学校上课",
        )

        prompt = harness.llm_prompts[0]
        self.assertIn("【当天已发生日程】", prompt)
        self.assertIn("在学校上课", prompt)
        self.assertIn("不代表当前位置或当前活动", prompt)
        self.assertIn("当前环境始终优先于历史日程", prompt)

    def test_explicit_time_category_affects_reference_score(self) -> None:
        request_text = "夜晚在街头拍照"
        wardrobe_intent = analyze_photo_wardrobe(request_text)
        night = _candidate(
            "night-street",
            outfit_category="",
            scene_categories=("outdoor",),
            time_categories=("night",),
            note="街头",
        )
        daytime = _candidate(
            "day-street",
            outfit_category="",
            scene_categories=("outdoor",),
            time_categories=("daytime",),
            note="街头",
        )

        night_score = ProactiveMessageMixin._photo_reference_candidate_score(
            night,
            request_text,
            "",
            wardrobe_intent=wardrobe_intent,
        )
        daytime_score = ProactiveMessageMixin._photo_reference_candidate_score(
            daytime,
            request_text,
            "",
            wardrobe_intent=wardrobe_intent,
        )

        self.assertGreater(night_score, daytime_score)

    def test_recent_generation_records_only_final_preset_and_keeps_hint_separate(self) -> None:
        harness = _ContinuityHarness()
        decision = PhotoWardrobeDecision(
            rule_id="explicit_prompt",
            preset_name="校服人像",
            selected_presets=("校服人像",),
            suggested_preset="居家睡衣",
            preset_source="wardrobe_category",
            suggestion_status="rejected_user_conflict",
        )

        harness._record_recent_photo_generation(
            trace_id="trace-1",
            session_key="session-1",
            workflow_kind="selfie",
            backend="test",
            ok=True,
            prompt_text="穿校服拍照",
            presets=["校服人像", "角色自拍"],
            wardrobe=decision,
            suggested_scene_preset="居家睡衣",
        )

        item = harness.data["recent_photo_generations"][0]
        self.assertEqual(item["schema_version"], 3)
        self.assertEqual(item["presets"], ["校服人像"])
        self.assertEqual(item["scene_preset"], "校服人像")
        self.assertEqual(item["preset_hint"], "居家睡衣")
        self.assertEqual(item["requested_scene_preset"], "居家睡衣")
        self.assertEqual(item["suggestion_status"], "rejected_user_conflict")

    def test_user_feedback_is_linked_to_recent_reference_plan_and_prompt(self) -> None:
        harness = _ContinuityHarness()
        harness._record_recent_photo_generation(
            trace_id="trace-feedback",
            session_key="session-feedback",
            continuity_key="conversation::user",
            workflow_kind="selfie",
            backend="test-backend",
            ok=True,
            prompt_text="final prompt text",
            prompt_hash="prompt-hash",
        )

        linked = harness._record_photo_reference_feedback(
            "脸不像，场景没换，请重新生成",
            continuity_key="conversation::user",
        )

        self.assertEqual(linked["generation_trace"], "trace-feedback")
        self.assertEqual(linked["backend"], "test-backend")
        self.assertEqual(linked["prompt_hash"], "prompt-hash")
        self.assertEqual(linked["final_prompt"], "final prompt text")
        self.assertEqual(linked["issues"], ["face_mismatch", "scene_not_changed"])
        self.assertTrue(linked["regenerate_requested"])
        generation = harness.data["recent_photo_generations"][0]
        self.assertTrue(generation["regeneration_requested"])
        self.assertEqual(
            generation["reference_feedback_issues"],
            ["face_mismatch", "scene_not_changed"],
        )

    def test_prompt_adapter_applies_at_most_one_scene_preset(self) -> None:
        harness = _ContinuityHarness()

        prompt, names = harness._apply_photo_generation_scene_presets(
            "base prompt",
            "selfie",
            preset_names=["校服人像", "角色自拍"],
        )

        self.assertEqual(names, ["校服人像"])
        self.assertIn("school uniform portrait", prompt)
        self.assertNotIn("casual character selfie", prompt)

    def test_rejected_hint_does_not_pollute_sent_photo_continuity(self) -> None:
        harness = _ContinuityHarness()
        continuity_key = "session-2|sender=user-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "generated.png"
            image_path.write_bytes(b"png")
            decision = PhotoWardrobeDecision(
                rule_id="explicit_prompt",
                category="school_uniform",
                lock_outfit=True,
                preset_name="校服人像",
                selected_presets=("校服人像",),
                suggested_preset="居家睡衣",
                preset_source="wardrobe_category",
                suggestion_status="rejected_user_conflict",
            )
            harness._record_recent_photo_generation(
                trace_id="trace-2",
                session_key="session-2",
                continuity_key=continuity_key,
                workflow_kind="selfie",
                backend="test",
                ok=True,
                prompt_text="穿校服拍照",
                image_path=str(image_path),
                presets=["校服人像"],
                wardrobe=decision,
                suggested_scene_preset="居家睡衣",
            )

            harness._annotate_recent_photo_generation(
                image_path=str(image_path),
                sent=True,
                preset_hint="居家睡衣",
            )

            item = harness.data["recent_photo_generations"][0]
            candidate = harness._recent_sent_photo_continuity_candidate(continuity_key)
            self.assertEqual(item["scene_preset"], "校服人像")
            self.assertEqual(item["preset_hint"], "居家睡衣")
            self.assertEqual(candidate["preferred_preset"], "校服人像")

    def test_schema_one_continuity_ignores_legacy_scene_preset(self) -> None:
        harness = _ContinuityHarness()
        continuity_key = "session-3|sender=user-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "legacy.png"
            image_path.write_bytes(b"png")
            store_key = harness._photo_continuity_store_key(continuity_key)
            harness.data["recent_photo_continuity"] = {
                store_key: {
                    "schema_version": 1,
                    "continuity_key": continuity_key,
                    "sent_at": time.time(),
                    "path": str(image_path),
                    "scene_preset": "居家睡衣",
                    "wardrobe_category": "school_uniform",
                }
            }

            candidate = harness._recent_sent_photo_continuity_candidate(continuity_key)

            self.assertEqual(candidate["preferred_preset"], "")

    def test_annotating_legacy_generation_does_not_promote_old_scene_hint(self) -> None:
        harness = _ContinuityHarness()
        continuity_key = "session-legacy|sender=user-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "legacy-generation.png"
            image_path.write_bytes(b"png")
            harness.data["recent_photo_generations"] = [
                {
                    "ok": True,
                    "sent": False,
                    "continuity_key": continuity_key,
                    "path": str(image_path),
                    "scene_preset": "居家睡衣",
                    "presets": [],
                    "wardrobe_category": "school_uniform",
                }
            ]

            harness._annotate_recent_photo_generation(
                image_path=str(image_path),
                sent=True,
                preset_hint="校服人像",
            )

            candidate = harness._recent_sent_photo_continuity_candidate(continuity_key)
            self.assertEqual(candidate["preferred_preset"], "")

    def test_schema_two_continuity_exposes_actual_final_preset(self) -> None:
        harness = _ContinuityHarness()
        continuity_key = "session-4|sender=user-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "current.png"
            image_path.write_bytes(b"png")
            store_key = harness._photo_continuity_store_key(continuity_key)
            harness.data["recent_photo_continuity"] = {
                store_key: {
                    "schema_version": 2,
                    "continuity_key": continuity_key,
                    "sent_at": time.time(),
                    "path": str(image_path),
                    "scene_preset": "校服人像",
                    "wardrobe_category": "school_uniform",
                }
            }

            candidate = harness._recent_sent_photo_continuity_candidate(continuity_key)

            self.assertEqual(candidate["preferred_preset"], "校服人像")


if __name__ == "__main__":
    unittest.main()
