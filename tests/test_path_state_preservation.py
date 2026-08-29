# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin
from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


def _long_image_path(filename: str = "persona  original.png") -> str:
    return "C:/reference/" + ("nested  folder/" * 36) + filename


class _RetryPathHarness(DailyStateMixin):
    @staticmethod
    def _ensure_planned_proactive_delivery_state(_user, *, now=None):
        current = float(now or 0)
        return {
            "freshness": "durable",
            "key": "durable-photo-retry",
            "expire_at": current + 7200,
        }

    @staticmethod
    def _latest_private_user_activity_ts(_user) -> float:
        return 0.0

    @staticmethod
    def _validate_proactive_outbound_candidate(text, **_kwargs):
        return {"decision": "send", "text": text}


class _AuditPathHarness(ProactiveEngineMixin):
    def __init__(self) -> None:
        self.data = {"proactive_audit_log": []}
        self.config = {}
        self.users: dict[str, dict] = {}

    @staticmethod
    def _planned_proactive_semantics(_user):
        return {}

    def _get_user(self, user_id: str) -> dict:
        return self.users.setdefault(str(user_id), {})


class PathStatePreservationTests(unittest.TestCase):
    def test_proactive_retry_keeps_long_image_path_and_internal_spaces(self) -> None:
        harness = _RetryPathHarness()
        user: dict = {}
        image_path = _long_image_path("retry  image.png")
        self.assertGreater(len(image_path), 500)

        result = harness._store_or_advance_proactive_send_retry(
            user,
            text="稍后再发这张图",
            image_path=image_path,
            extra_components=[],
            reason="creative_share",
            action="message",
            action_summary="发送创作配图",
            error_text="timeout",
            now=1000.0,
        )

        self.assertIn("已保留待重发内容", result)
        self.assertEqual(user["pending_proactive_send_retry"]["image_path"], image_path)

    def test_proactive_audit_and_photo_counter_keep_exact_image_path(self) -> None:
        harness = _AuditPathHarness()
        image_path = _long_image_path("generated  photo.png")
        audit_id = harness._append_proactive_audit(
            "10001",
            {"user_id": "10001"},
            status="running",
        )

        harness._update_proactive_audit(
            audit_id,
            status="sent",
            image_path=image_path,
        )
        harness._note_photo_generation_attempt("10001", image_path)

        self.assertEqual(harness.data["proactive_audit_log"][0]["image_path"], image_path)
        self.assertEqual(harness.users["10001"]["last_generated_photo_path"], image_path)

    def test_qzone_generated_image_state_keeps_exact_paths(self) -> None:
        harness = QzoneMixin()
        state: dict = {}
        image_path = _long_image_path("qzone  image.png")
        reference_path = _long_image_path("qzone  reference.png")

        harness._qzone_note_publish_image_status(
            state,
            "life_publish",
            "generated",
            path=image_path,
            reference_image=reference_path,
            reference_exists=True,
        )

        self.assertEqual(state["last_life_publish_generated_image_path"], image_path)
        self.assertEqual(state["last_life_publish_generated_image_reference"], reference_path)

    def test_page_path_resolvers_accept_legal_internal_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "album  cover.png"
            image_path.write_bytes(b"cover")
            api = PrivateCompanionPageApi(SimpleNamespace(data_dir=directory))

            self.assertEqual(api._resolve_bookshelf_data_file(str(image_path)), image_path.resolve())
            self.assertEqual(
                api._creative_project_cover_path({"cover_path": str(image_path)}),
                image_path.resolve(),
            )

    def test_recent_photo_summary_keeps_debug_and_output_paths_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            debug_root = Path(directory) / "photo_prompt_debug"
            debug_root.mkdir()
            prompt_path = debug_root / "trace  prompt.json"
            prompt_path.write_text(
                json.dumps(
                    {
                        "final_prompt": "complete prompt",
                        "workflow_fixed_prompt": {
                            "scope": "selfie",
                            "config_key": "photo_generation_selfie_fixed_prompt",
                            "configured": True,
                            "normalized": True,
                            "normalization_changed": True,
                            "conflict_cleaned": True,
                            "cleaned": True,
                            "applied": True,
                            "raw_length": 42,
                            "normalized_length": 36,
                            "applied_length": 28,
                            "removed_rules": ["incompatible_wardrobe"],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_path = _long_image_path("summary  image.png")
            plugin = SimpleNamespace(
                data_dir=directory,
                _format_timestamp_elapsed=lambda _ts: "刚刚",
            )
            api = PrivateCompanionPageApi(plugin)

            items = api._recent_photo_generation_summary(
                {
                    "recent_photo_generations": [
                        {
                            "ts": 1,
                            "ok": True,
                            "prompt_path": str(prompt_path),
                            "path": output_path,
                        }
                    ]
                }
            )

            self.assertEqual(items[0]["prompt_path"], str(prompt_path))
            self.assertEqual(items[0]["path"], output_path)
            self.assertEqual(items[0]["full_prompt"], "complete prompt")
            self.assertEqual(
                items[0]["workflow_fixed_prompt"],
                {
                    "scope": "selfie",
                    "config_key": "photo_generation_selfie_fixed_prompt",
                    "configured": True,
                    "normalized": True,
                    "normalization_changed": True,
                    "conflict_cleaned": True,
                    "cleaned": True,
                    "applied": True,
                    "raw_length": 42,
                    "normalized_length": 36,
                    "applied_length": 28,
                    "removed_rules": ["incompatible_wardrobe"],
                },
            )


if __name__ == "__main__":
    unittest.main()
