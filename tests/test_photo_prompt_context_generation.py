from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "astrbot_plugin_private_companion"


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class _Dummy:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(self, *args, **kwargs):
        return _Dummy()

    def __getattr__(self, _name: str):
        return _Dummy()


def _astrbot_stubs() -> dict[str, types.ModuleType]:
    names = (
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.message_components",
        "astrbot.api.provider",
        "astrbot.api.star",
        "astrbot.core",
        "astrbot.core.message",
        "astrbot.core.message.components",
        "astrbot.core.astr_main_agent",
        "astrbot.core.agent",
        "astrbot.core.agent.message",
        "astrbot.core.db",
        "astrbot.core.db.po",
        "astrbot.core.platform",
        "astrbot.core.platform.astrbot_message",
        "astrbot.core.platform.message_session",
        "astrbot.core.platform.message_type",
        "astrbot.core.platform.platform",
        "astrbot.core.platform.platform_metadata",
        "astrbot.core.star",
        "astrbot.core.star.star_handler",
        "astrbot.core.provider",
        "astrbot.core.provider.entities",
        "astrbot.core.utils",
        "astrbot.core.utils.astrbot_path",
    )
    modules = {name: types.ModuleType(name) for name in names}
    for module in modules.values():
        module.__path__ = []

    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = _Logger()
    modules["astrbot.api.event"].AstrMessageEvent = _Dummy
    modules["astrbot.api.event"].MessageChain = _Dummy
    modules["astrbot.api.event"].filter = _Dummy()
    for name in ("At", "Image", "Plain", "Record", "Reply"):
        setattr(modules["astrbot.api.message_components"], name, _Dummy)
    modules["astrbot.api.provider"].ProviderRequest = _Dummy
    for name in ("Context", "Star", "StarTools", "register"):
        setattr(modules["astrbot.api.star"], name, _Dummy)
    modules["astrbot.core"].file_token_service = _Dummy()
    for name in ("MainAgentBuildConfig", "build_main_agent"):
        setattr(modules["astrbot.core.astr_main_agent"], name, _Dummy)
    for name in ("AssistantMessageSegment", "TextPart", "UserMessageSegment"):
        setattr(modules["astrbot.core.agent.message"], name, _Dummy)
    modules["astrbot.core.db.po"].Conversation = _Dummy
    symbol_groups = {
        "astrbot.core.platform.astrbot_message": ("AstrBotMessage", "MessageMember"),
        "astrbot.core.platform.message_session": ("MessageSession",),
        "astrbot.core.platform.message_type": ("MessageType",),
        "astrbot.core.platform.platform": ("PlatformStatus",),
        "astrbot.core.platform.platform_metadata": ("PlatformMetadata",),
        "astrbot.core.star.star_handler": ("EventType", "star_handlers_registry"),
        "astrbot.core.provider.entities": ("LLMResponse",),
    }
    for module_name, symbols in symbol_groups.items():
        for symbol in symbols:
            setattr(modules[module_name], symbol, _Dummy)
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = tempfile.gettempdir
    return modules


if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PLUGIN_ROOT)]
    package.__package__ = PACKAGE_NAME
    sys.modules[PACKAGE_NAME] = package

with mock.patch.dict(sys.modules, _astrbot_stubs()):
    from astrbot_plugin_private_companion.photo_prompt_context import PhotoPromptSection
    from astrbot_plugin_private_companion.photo_reference_plan import (
        PhotoReferencePlan,
        ReferenceBinding,
    )
    from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _PhotoGenerationHarness(ProactiveMessageMixin):
    def __init__(self, output_path: str) -> None:
        self.data: dict = {}
        self.photo_generation_backend = "comfyui"
        self.photo_generation_prompt_format = "traditional"
        self.photo_generation_fixed_prompt = "fine film grain"
        self.photo_generation_scene_presets = ""
        self.comfyui_selfie_workflow_name = "selfie-workflow"
        self.comfyui_text2img_workflow_name = ""
        self.output_path = output_path
        self.persona_path = ""
        self.backend_calls: list[dict[str, str]] = []
        self.data_dir = str(Path(output_path).parent)
        self.photo_generation_trace_max_size_kb = 2048
        self.photo_generation_trace_backup_count = 2
        self.dialogue_scene_hint = "Identity: Alice; Today's outfit: blue pajamas; Current location: classroom"

    async def _photo_persona_reference_image_path_async(self) -> str:
        return self.persona_path

    def _photo_generation_selfie_schedule_scene_hint(self, _user_id: str = "") -> str:
        return self.dialogue_scene_hint

    @staticmethod
    async def _photo_reference_candidate_for_path_async(
        reference_image_path: str,
        **_kwargs,
    ) -> dict:
        return {
            "id": "sleepwear-selfie",
            "path": reference_image_path,
            "source": reference_image_path,
            "kind": "library",
            "reference_roles": ["identity", "outfit"],
            "outfit_category": "sleepwear",
            "outfit_lock_default": True,
        }

    @staticmethod
    def _photo_generation_scene_presets() -> dict[str, str]:
        return {"conflicting preset": "cozy pajamas portrait; warm window light"}

    @staticmethod
    def _apply_photo_generation_scene_presets(
        _prompt_text: str,
        _workflow_kind: str,
        *,
        preset_names: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        return "Scene preset: cozy pajamas portrait; warm window light", list(
            preset_names or ["conflicting preset"]
        )

    @staticmethod
    def _photo_generation_recent_continuity_constraint(
        _workflow_kind: str,
        **_kwargs,
    ) -> tuple[str, bool]:
        return (
            "Recent-photo continuity: preserve identity, face, hairstyle, "
            "and the exact pajamas outfit and accessories.",
            True,
        )

    @staticmethod
    def _write_photo_prompt_debug_file(**_kwargs) -> tuple[str, str]:
        return "", "test-prompt-hash"

    @staticmethod
    def _photo_generation_backend_config_summary() -> str:
        return "test-backend"

    @staticmethod
    def _comfyui_photo_available() -> bool:
        return True

    @staticmethod
    def _local_photo_generation_busy_state(*, force_refresh: bool = False):
        return None

    async def _run_comfyui_photo_workflow(
        self,
        workflow_name: str,
        prompt_text: str,
        *,
        session_key: str,
        reference_image_path: str = "",
        reference_image_paths=(),
    ) -> tuple[str, str]:
        self.backend_calls.append(
            {
                "workflow": workflow_name,
                "prompt": prompt_text,
                "session": session_key,
                "reference": reference_image_path,
                "references": list(reference_image_paths),
            }
        )
        return self.output_path, "generated"

    def _save_data_sync(self) -> None:
        pass


class PhotoPromptContextGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_debug_log_keeps_complete_prompt_without_changing_backend_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            complete_preset = "Scene preset: " + ("cozy bedroom detail, " * 40) + "PRESET_END"
            harness._apply_photo_generation_scene_presets = lambda *_args, **_kwargs: (
                complete_preset,
                ["long preset"],
            )
            harness._write_photo_prompt_debug_file = lambda **kwargs: (
                ProactiveMessageMixin._write_photo_prompt_debug_file(harness, **kwargs)
            )

            await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="Take a natural selfie in the bedroom.",
                session_key="complete-prompt-log",
            )

            backend_prompt = harness.backend_calls[0]["prompt"]
            debug_path = next((Path(directory) / "photo_prompt_debug").glob("*.json"))
            debug_payload = json.loads(debug_path.read_text(encoding="utf-8"))
            logged_prompt = debug_payload["final_prompt"]
            trace_events = [
                json.loads(line)
                for line in (Path(directory) / "photo_generation_trace.txt")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            trace_prompt_event = next(
                event for event in trace_events if event["stage"] == "prompt_composed"
            )
            recent = harness.data["recent_photo_generations"][0]

            self.assertIn("[section compacted]", backend_prompt)
            self.assertNotIn("[section compacted]", logged_prompt)
            self.assertIn(complete_preset, logged_prompt)
            self.assertEqual(trace_prompt_event["data"]["prompt"], logged_prompt)
            self.assertEqual(trace_prompt_event["data"]["submitted_prompt"], backend_prompt)
            self.assertLessEqual(len(recent["prompt"]), 900)
            self.assertEqual(recent["complete_prompt_length"], len(logged_prompt))
            self.assertEqual(recent["submitted_prompt_length"], len(backend_prompt))
            self.assertEqual(
                recent["prompt_hash"],
                hashlib.sha256(logged_prompt.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                recent["submitted_prompt_hash"],
                hashlib.sha256(backend_prompt.encode("utf-8")).hexdigest(),
            )

    async def test_schedule_history_reaches_selection_but_not_final_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            captured: dict[str, object] = {}
            history = "08:00-09:00｜已完成｜在学校上课｜情绪：专注"
            harness._photo_reference_schedule_history_context = lambda: history

            async def select_plan(*_args, **kwargs):
                captured.update(kwargs)
                return PhotoReferencePlan((), "", "no_usable_reference", "")

            harness._select_photo_reference_plan_async = select_plan

            await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="晚上在卧室随手自拍",
                session_key="history-selection-only",
            )

        self.assertEqual(captured["schedule_history_context"], history)
        self.assertNotIn(history, captured["ambient_context"])
        self.assertNotIn("在学校上课", harness.backend_calls[0]["prompt"])

    async def test_successful_generation_writes_complete_trace_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="Take a natural selfie by the window.",
                session_key="trace-chain-session",
                continuity_key="trace-chain-continuity",
            )

            trace_path = Path(directory) / "photo_generation_trace.txt"
            events = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            expected_stages = [
                "request_received",
                "reference_intent_analyzed",
                "reference_plan_built",
                "reference_plan_projected",
                "wardrobe_resolved",
                "prompt_composed",
                "backend_selected",
                "output_validated",
                "completed",
            ]
            stages = [event["stage"] for event in events]
            stage_positions = [stages.index(stage) for stage in expected_stages]

            self.assertEqual(backend, "ComfyUI")
            self.assertEqual(image_path, str(output))
            self.assertEqual(stage_positions, sorted(stage_positions))
            self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
            self.assertEqual(len({event["trace"] for event in events}), 1)
            self.assertTrue(all(event["context"]["session"] == "trace-chain-session" for event in events))
            self.assertTrue(all(event["context"]["workflow_kind"] == "selfie" for event in events))
            self.assertEqual(events[-1]["stage"], "completed")
            self.assertEqual(events[-1]["status"], "ok")
            self.assertEqual(events[-1]["context"]["backend"], "ComfyUI")
            self.assertEqual(events[-1]["data"]["image_path"], str(output))

    async def test_matching_outfit_reference_reaches_debug_trace_responsibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "sleepwear.png"
            output = root / "generated.png"
            reference.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            harness.enable_photo_reference_image = True
            candidate = {
                "id": "sleepwear-reference",
                "kind": "library",
                "path": str(reference),
                "source": str(reference),
                "reference_roles": ["identity", "outfit"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
            }

            async def candidates(*, allow_daily_outfit: bool = True):
                return [dict(candidate)]

            harness._photo_reference_candidates_async = candidates
            await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="换上睡衣",
                session_key="matching-outfit-debug-trace",
            )

            events = [
                json.loads(line)
                for line in (root / "photo_generation_trace.txt")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        expected = (
            "Reference responsibility: effective roles=identity, outfit; "
            "outfit category=sleepwear."
        )
        intent_event = next(
            event for event in events if event["stage"] == "reference_intent_analyzed"
        )
        plan_event = next(
            event for event in events if event["stage"] == "reference_plan_built"
        )
        prompt_event = next(
            event for event in events if event["stage"] == "prompt_composed"
        )
        self.assertEqual(intent_event["data"]["excluded_roles"], ["outfit"])
        self.assertEqual(
            plan_event["data"]["bindings"][0]["roles"], ["identity", "outfit"]
        )
        self.assertIn(expected, prompt_event["data"]["prompt"])

    async def test_backend_receives_physically_sanitized_context_and_reference(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "sleepwear-selfie.png"
            output = root / "generated.png"
            reference.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="Please draw me wearing a school uniform.",
                session_key="test-session",
                continuity_key="test-continuity",
                reference_image_path=str(reference),
                prompt_sections=(
                    PhotoPromptSection(
                        name="user_request",
                        source="user_request",
                        positive="Please draw me wearing a school uniform.",
                        protected=True,
                    ),
                    PhotoPromptSection(
                        name="input_scene",
                        source="scene_context",
                        positive=(
                            "Identity: Alice; Today's outfit: blue pajamas; "
                            "Current location: classroom"
                        ),
                    ),
                    PhotoPromptSection(
                        name="duplicate_context",
                        source="scene_context",
                        positive="first neutral detail",
                    ),
                    PhotoPromptSection(
                        name="duplicate_context",
                        source="scene_context",
                        positive="second neutral detail",
                    ),
                ),
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(len(harness.backend_calls), 1)
        submitted = harness.backend_calls[0]
        submitted_prompt = submitted["prompt"]
        self.assertEqual(submitted["reference"], "")
        self.assertIn("school uniform", submitted_prompt.lower())
        self.assertIn("current location: classroom", submitted_prompt.lower())
        self.assertIn("warm window light", submitted_prompt.lower())
        self.assertIn("fine film grain", submitted_prompt.lower())
        self.assertNotIn("pajama", submitted_prompt.lower())
        self.assertNotIn("sleepwear", submitted_prompt.lower())
        self.assertNotIn("exact outfit and accessories", submitted_prompt.lower())
        self.assertNotIn("Conflict resolution", submitted_prompt)

        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["residual_conflicts"], [])
        self.assertTrue(recorded["reference_removed"])
        self.assertEqual(recorded["reference_removal"]["rule"], "reference_outfit_conflict")
        self.assertTrue(recorded["detected_conflicts"])
        self.assertTrue(recorded["removed_conflict_details"])
        self.assertEqual(recorded["residual_conflict_details"], [])
        self.assertTrue(
            all(item.get("sha256") for item in recorded["removed_conflict_details"])
        )
        self.assertEqual(recorded["reference_path"], "")
        for section in ("input_scene", "scene_preset", "global_fixed_prompt"):
            self.assertNotIn("pajama", recorded["prompt_sections"][section].lower())
        self.assertEqual(recorded["prompt_sections"]["duplicate_context"], "first neutral detail")
        self.assertEqual(recorded["prompt_sections"]["duplicate_context#2"], "second neutral detail")
        self.assertNotIn("recent_continuity", recorded["prompt_sections"])

    async def test_protected_fixed_rules_reach_backend_without_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            fixed_prompt = (
                "Overall Physique: preserve complete body proportions and stable facial structure; "
                "Lip Color: preserve the exact natural lip color without simplification or substitution."
            )
            harness.photo_generation_fixed_prompt = fixed_prompt
            _composition_positive, composition_negative = (
                harness._photo_generation_composition_sections(
                    "selfie",
                    "拍一张自然自拍",
                )
            )

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="拍一张自然自拍",
                session_key="protected-fixed-rules",
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        submitted_prompt = harness.backend_calls[0]["prompt"]
        self.assertIn(
            "Overall Physique: preserve complete body proportions and stable facial structure",
            submitted_prompt,
        )
        self.assertIn(
            "Lip Color: preserve the exact natural lip color without simplification or substitution.",
            submitted_prompt,
        )
        self.assertIn(composition_negative, submitted_prompt)
        self.assertNotIn("[section compacted]", submitted_prompt)

    async def test_locked_sleepwear_keeps_complete_global_fixed_prompt_at_backend(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "sleepwear.png"
            output = root / "generated.png"
            reference.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            fixed_prompt = (
                "Preserve the established attire, complete body proportions, and exact natural lip color "
                "without simplification or substitution."
            )
            harness.photo_generation_fixed_prompt = fixed_prompt

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="change into sleepwear, take a selfie in the bedroom",
                session_key="locked-sleepwear-fixed-prompt",
                reference_image_path=str(reference),
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        submitted_prompt = harness.backend_calls[0]["prompt"]
        self.assertIn(f"Additional fixed prompt: {fixed_prompt}", submitted_prompt)
        record = harness.data["recent_photo_generations"][0]
        self.assertTrue(record["outfit_locked"])
        self.assertEqual(record["wardrobe_category"], "sleepwear")
        self.assertFalse(
            any(
                item.get("source") == "fixed_prompt"
                and item.get("rule") == "unverified_wardrobe"
                for item in record["removed_conflict_details"]
            )
        )
        self.assertFalse(
            any(
                value.startswith("fixed_prompt:unverified_wardrobe")
                for value in record["removed_conflicts"]
            )
        )

    def test_prompt_clip_never_returns_a_partial_word(self) -> None:
        self.assertEqual(
            _PhotoGenerationHarness._photo_prompt_clip("duplicate character", 3),
            "",
        )

    async def test_long_protected_user_request_reaches_backend_in_full(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            user_request = " ".join(f"detail{index:04d}" for index in range(320))

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text=user_request,
                session_key="long-protected-user-request",
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertIn(user_request, harness.backend_calls[0]["prompt"])

    async def test_fresh_image_request_never_submits_an_explicit_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "old.png"
            output = root / "generated.png"
            reference.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="不要参考图，生成全新画面",
                session_key="fresh-session",
                reference_image_path=str(reference),
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["reference"], "")
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_intent"]["continuity_mode"], "new_topic")
        self.assertEqual(recorded["reference_plan"]["bindings"], [])

    async def test_explicit_character_reference_rejection_stays_text_only_with_persona_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            persona = root / "persona.png"
            output = root / "generated.png"
            persona.write_bytes(b"persona")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            harness.persona_path = str(persona)

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="不要使用人物参考，按文字生成自然自拍",
                session_key="no-character-reference",
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["reference"], "")
        self.assertEqual(harness.backend_calls[0]["references"], [])
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_intent"]["requested_roles"], [])
        self.assertEqual(recorded["reference_intent"]["excluded_roles"], ["identity"])
        self.assertEqual(recorded["reference_plan"]["bindings"], [])

    async def test_edit_without_source_stops_before_backend_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, note = await harness._generate_photo_image(
                workflow_kind="edit",
                prompt_text="把这张改成动漫风",
                session_key="edit-session",
            )

        self.assertEqual(backend, "参考图")
        self.assertEqual(image_path, "")
        self.assertIn("停止改图", note)
        self.assertEqual(harness.backend_calls, [])
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], ["source"])

    async def test_multi_image_roles_are_planned_and_single_backend_uses_primary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            references = [root / name for name in ("face.png", "clothes.png", "pose.png")]
            for reference in references:
                reference.write_bytes(b"reference")
            output = root / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="用第一张的脸，第二张的衣服，第三张的姿势",
                session_key="multi-reference-session",
                reference_image_paths=[str(path) for path in references],
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["reference"], str(references[0]))
        self.assertIn("outfit", harness.backend_calls[0]["prompt"])
        self.assertIn("pose", harness.backend_calls[0]["prompt"])
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(len(recorded["reference_plan"]["bindings"]), 3)
        self.assertEqual(
            recorded["reference_plan"]["submitted_reference_ids"],
            ["explicit_reference_1"],
        )
        self.assertEqual(
            recorded["reference_fallback"]["missing_roles"],
            ["outfit", "pose"],
        )

    async def test_multi_image_backend_receives_every_planned_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            references = [root / name for name in ("face.png", "clothes.png", "pose.png")]
            for reference in references:
                reference.write_bytes(b"reference")
            output = root / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            harness._photo_reference_backend_max_images = lambda _kind, **_kwargs: 3

            async def run_comfyui(
                workflow_name,
                prompt_text,
                *,
                session_key,
                reference_image_path="",
                reference_image_paths=(),
            ):
                harness.backend_calls.append(
                    {
                        "workflow": workflow_name,
                        "prompt": prompt_text,
                        "session": session_key,
                        "reference": reference_image_path,
                        "references": list(reference_image_paths),
                    }
                )
                return str(output), "generated"

            harness._run_comfyui_photo_workflow = run_comfyui
            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="用第一张的脸，第二张的衣服，第三张的姿势",
                session_key="multi-capable-backend",
                reference_image_paths=[str(path) for path in references],
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["references"], [str(path) for path in references])
        self.assertIn("reference image 1: identity", harness.backend_calls[0]["prompt"])
        self.assertIn("reference image 2: outfit", harness.backend_calls[0]["prompt"])
        self.assertIn("reference image 3: pose", harness.backend_calls[0]["prompt"])
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(
            recorded["reference_plan"]["submitted_reference_ids"],
            ["explicit_reference_1", "explicit_reference_2", "explicit_reference_3"],
        )
        self.assertTrue(
            all(binding["submitted"] for binding in recorded["reference_plan"]["bindings"])
        )
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], [])

    async def test_multi_image_edit_keeps_source_and_outfit_roles_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            outfit = root / "outfit.png"
            output = root / "generated.png"
            for path in (source, outfit, output):
                path.write_bytes(b"image")
            harness = _PhotoGenerationHarness(str(output))
            harness._photo_reference_backend_max_images = lambda _kind, **_kwargs: 2

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="edit",
                prompt_text="第一张作为原图，只参考第二张的衣服",
                session_key="multi-edit-reference",
                reference_image_paths=[str(source), str(outfit)],
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["references"], [str(source), str(outfit)])
        recorded = harness.data["recent_photo_generations"][0]
        bindings = recorded["reference_plan"]["bindings"]
        self.assertEqual(bindings[0]["roles"], ["source"])
        self.assertEqual(bindings[1]["roles"], ["outfit"])
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], [])

    async def test_comfyui_adapter_submits_all_images_to_exact_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            references = [root / name for name in ("face.png", "clothes.png", "pose.png")]
            for reference in references:
                reference.write_bytes(b"reference")
            workflow_file = root / "selfie-three-images.json"
            workflow_file.write_text("{}", encoding="utf-8")
            output = root / "generated.png"
            output.write_bytes(b"generated")
            submitted: dict[str, object] = {}

            class FakeWorkflow:
                def __init__(self, server_ip, client_id) -> None:
                    submitted["connection"] = (server_ip, client_id)

                def load_workflow_api(self, path) -> None:
                    submitted["workflow_file"] = path

                async def submit_only(self, images, texts, videos, *, debug=False):
                    submitted["images"] = list(images)
                    submitted["texts"] = list(texts)
                    submitted["videos"] = list(videos)
                    submitted["debug"] = debug
                    return "prompt-1"

            def find_workflow_file(name, texts, images, videos, workflow_dir):
                if (name, texts, images, videos, str(workflow_dir)) == (
                    "selfie-workflow",
                    1,
                    3,
                    0,
                    str(root),
                ):
                    return str(workflow_file)
                return ""

            async def get_result(_server_ip, _prompt_id):
                return "https://example.invalid/generated.png", "image", []

            async def download_image(_url):
                return str(output)

            async def save_image(path, _session_key):
                return path

            module = types.SimpleNamespace(
                _plugin_config={"debug_mode": False},
                _get_server_config=lambda _config: ("http://comfyui", "client-1"),
                _get_workflow_dir=lambda: str(root),
                find_workflow_file=find_workflow_file,
                ComfyUIWorkflow=FakeWorkflow,
                _get_result_for_prompt=get_result,
                _download_image_to_temp=download_image,
                _save_image_to_persistent_path=save_image,
            )
            harness = _PhotoGenerationHarness(str(output))
            harness.comfyui_photo_wait_seconds = 1
            harness._get_comfyui_module = lambda: module

            capacity = harness._photo_reference_backend_max_images(
                "selfie",
                requested_images=3,
            )
            image_path, note = await ProactiveMessageMixin._run_comfyui_photo_workflow(
                harness,
                "selfie-workflow",
                "portrait",
                session_key="adapter-multi-image",
                reference_image_path=str(references[0]),
                reference_image_paths=[str(path) for path in references],
            )

        self.assertEqual(capacity, 3)
        self.assertEqual(image_path, str(output))
        self.assertIn("3 张本地参考图", note)
        self.assertEqual(submitted["workflow_file"], str(workflow_file))
        self.assertEqual(submitted["images"], [str(path) for path in references])
        self.assertEqual(submitted["texts"], ["portrait"])

    async def test_invalid_multi_image_binding_is_skipped_with_missing_role_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing_face = root / "missing-face.png"
            outfit = root / "outfit.png"
            output = root / "generated.png"
            outfit.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="用第一张的脸，第二张的衣服",
                session_key="invalid-multi-reference-session",
                reference_image_paths=[str(missing_face), str(outfit)],
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["reference"], str(outfit))
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_plan"]["primary_reference_id"], "explicit_reference_2")
        self.assertEqual(recorded["reference_fallback"]["fulfilled_roles"], ["outfit"])
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], ["identity"])

    async def test_failed_binding_resolution_submits_persona_as_final_identity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-reference.png"
            persona = root / "persona.png"
            output = root / "generated.png"
            persona.write_bytes(b"persona")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            harness.persona_path = str(persona)
            broken_plan = PhotoReferencePlan(
                bindings=(
                    ReferenceBinding(
                        reference_id="broken-reference",
                        path=str(missing),
                        roles=("identity",),
                        priority=500,
                        preserve=("identity",),
                        ignore=(),
                    ),
                ),
                primary_reference_id="broken-reference",
                selection_reason="highest_priority_role_match",
                fallback_reason="",
            )

            async def select_plan(*_args, **_kwargs):
                return broken_plan

            async def reject_binding(*_args, **_kwargs):
                return {}

            harness._select_photo_reference_plan_async = select_plan
            harness._photo_reference_candidate_from_plan_binding_async = reject_binding

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="拍一张自然自拍",
                session_key="persona-final-fallback",
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["reference"], str(persona))
        self.assertEqual(harness.backend_calls[0]["references"], [str(persona)])
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_plan"]["primary_reference_id"], "persona")
        self.assertEqual(
            recorded["reference_plan"]["submitted_reference_ids"],
            ["persona"],
        )
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], [])

    async def test_reference_is_textually_downgraded_for_sdgen_backend(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "face.png"
            output = root / "generated.png"
            reference.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            harness.photo_generation_backend = "sdgen"
            harness._sdgen_photo_available = lambda: True

            async def run_sdgen(prompt_text, *, session_key):
                harness.backend_calls.append(
                    {
                        "workflow": "sdgen",
                        "prompt": prompt_text,
                        "session": session_key,
                        "reference": "",
                    }
                )
                return str(output), "generated"

            harness._run_sdgen_photo_generation = run_sdgen

            backend, image_path, note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="只参考脸",
                session_key="sdgen-reference-fallback",
                reference_image_path=str(reference),
            )

        self.assertEqual(backend, "SDGen")
        self.assertEqual(image_path, str(output))
        self.assertEqual(harness.backend_calls[0]["reference"], "")
        self.assertIn("人物身份", note)
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_plan"]["submitted_reference_ids"], [])
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], ["identity"])

    async def test_optional_selfie_identity_does_not_emit_missing_reference_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.png"
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))

            backend, image_path, note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="随手拍一张",
                session_key="optional-identity",
            )

        self.assertEqual(backend, "ComfyUI")
        self.assertEqual(image_path, str(output))
        self.assertEqual(note, "generated")
        recorded = harness.data["recent_photo_generations"][0]
        self.assertEqual(recorded["reference_intent"]["source"], "workflow_default")
        self.assertEqual(recorded["reference_fallback"]["missing_roles"], [])
        self.assertEqual(recorded["reference_fallback"]["message"], "")

    async def test_textual_dialogue_outfit_beats_daily_outfit_on_continue_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "daily-outfit.png"
            output = root / "generated.png"
            reference.write_bytes(b"reference")
            output.write_bytes(b"generated")
            harness = _PhotoGenerationHarness(str(output))
            harness.dialogue_scene_hint = "时间：下午；对话最新服装：换一套JK校服；当天基础穿搭：白衬衫"

            backend, image_path, _note = await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="继续拍一张",
                request_text="继续拍一张",
                requester_user_id="10001",
                requester_is_private=True,
                session_key="dialogue-outfit",
                reference_image_path=str(reference),
            )

        self.assertEqual("ComfyUI", backend)
        self.assertEqual(str(output), image_path)
        submitted_prompt = harness.backend_calls[0]["prompt"].lower()
        self.assertIn("school uniform", submitted_prompt)
        self.assertNotIn("pajama", submitted_prompt)
        self.assertEqual("", harness.backend_calls[0]["reference"])
        record = harness.data["recent_photo_generations"][0]
        self.assertTrue(record["daily_outfit_removed"])


if __name__ == "__main__":
    unittest.main()
