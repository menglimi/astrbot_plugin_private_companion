from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_asset_gate import (  # noqa: E402
    CONTRACT_NAME,
    MAX_INPUT_ASSETS,
    ReferenceAssetGate,
)


class ReferenceAssetGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tempdir.name)
        self.asset_dir = self.data_dir / "photo_reference_assets"
        self.asset_dir.mkdir()
        self.gate = ReferenceAssetGate(self.data_dir, now=lambda: 1000.0)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _entry(self, name: str, role: str, **extra: object) -> dict[str, object]:
        path = self.asset_dir / name
        path.write_bytes(f"image:{name}".encode("utf-8"))
        payload: dict[str, object] = {
            "id": name.rsplit(".", 1)[0],
            "role": role,
            "file": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        payload.update(extra)
        return payload

    def test_only_registered_local_hashed_assets_are_accepted(self) -> None:
        valid = self._entry("identity.png", "identity")
        asset, status = self.gate.validate_entry(valid)
        self.assertEqual("ok", status)
        self.assertEqual("identity", asset.role if asset else "")

        for source in (
            {**valid, "file": "../identity.png"},
            {**valid, "file": "C:/outside.png"},
            {**valid, "file": "https://example.invalid/identity.png"},
            {**valid, "sha256": "0" * 64},
            {**valid, "role": "source"},
        ):
            rejected, rejected_status = self.gate.validate_entry(source)
            self.assertIsNone(rejected)
            self.assertNotEqual("ok", rejected_status)

    def test_roles_are_deduplicated_and_new_topic_is_stably_ordered(self) -> None:
        entries = [
            self._entry("style.png", "style"),
            self._entry("scene.png", "scene"),
            self._entry("identity.png", "identity"),
            self._entry("outfit.png", "outfit", outfit_lock_default=True),
            self._entry("pose.png", "pose"),
            self._entry("identity-duplicate.png", "identity"),
        ]
        plan, status = self.gate.plan(entries, generation_id="gen-1", mode="new_topic")
        self.assertEqual("ok", status)
        self.assertIsNotNone(plan)
        self.assertEqual(["identity", "outfit", "pose", "scene"], [item.role for item in plan.assets])
        self.assertLessEqual(len(plan.assets), MAX_INPUT_ASSETS)

    def test_continuation_and_edit_only_add_explicitly_enabled_roles(self) -> None:
        entries = [
            self._entry("identity.png", "identity"),
            self._entry("outfit.png", "outfit", outfit_lock_default=True),
            self._entry("pose.png", "pose", continuation_match=True),
            self._entry("scene.png", "scene", continuation_match=False, edit_enabled=True),
        ]
        continuation, status = self.gate.plan(entries, generation_id="continue", mode="continuation")
        self.assertEqual("ok", status)
        self.assertEqual(["identity", "outfit", "pose"], [item.role for item in continuation.assets])

        edit, status = self.gate.plan(entries, generation_id="edit", mode="edit")
        self.assertEqual("ok", status)
        self.assertEqual(["identity", "scene"], [item.role for item in edit.assets])

    def test_ticket_is_opaque_one_shot_and_generation_backend_bound(self) -> None:
        entries = [self._entry("identity.png", "identity")]
        plan, status = self.gate.plan(entries, generation_id="gen-2", mode="new_topic")
        self.assertEqual("ok", status)
        ticket = self.gate.issue(plan, backend="comfyui")
        self.assertIsNotNone(ticket)
        denied, denied_status = self.gate.consume(ticket, generation_id="other", backend="comfyui", capacity=1)
        self.assertEqual([], denied)
        self.assertEqual("scope_mismatch", denied_status)

        paths, consume_status = self.gate.consume(ticket, generation_id="gen-2", backend="comfyui", capacity=1)
        self.assertEqual("ok", consume_status)
        self.assertEqual(1, len(paths))
        reused, reused_status = self.gate.consume(ticket, generation_id="gen-2", backend="comfyui", capacity=1)
        self.assertEqual([], reused)
        self.assertEqual("expired_or_consumed_ticket", reused_status)

    def test_projection_does_not_disclose_path_or_hash(self) -> None:
        entry = self._entry("identity.png", "identity")
        projection = self.gate.public_projection(
            [entry, {"id": "C:/sensitive-path.png", "role": "identity", "file": "identity.png", "sha256": entry["sha256"]}],
            backend_capacity=4,
        )
        encoded = repr(projection)
        self.assertEqual(CONTRACT_NAME, projection["contract"])
        self.assertNotIn(str(self.asset_dir), encoded)
        self.assertNotIn(str(entry["sha256"]), encoded)
        self.assertNotIn("sensitive-path", encoded)
        self.assertEqual("identity", projection["items"][0]["role"])

    def test_runtime_and_panel_have_only_q5_gate_call_sites(self) -> None:
        image_spec = find_spec("astrbot_plugin_image_companion")
        image_root = (
            Path(image_spec.origin).resolve().parent
            if image_spec is not None and image_spec.origin
            else None
        )
        runtime_path = (
            image_root / "image_runtime.py"
            if image_root is not None
            else ROOT / "proactive_message.py"
        )
        proactive = runtime_path.read_text(encoding="utf-8")
        page_api = (ROOT / "page_api.py").read_text(encoding="utf-8")
        frontend = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        self.assertIn("reference_asset_ticket", proactive)
        self.assertIn("structured_reference_count", proactive)
        if image_root is not None:
            companion = (ROOT / "proactive_message.py").read_text(encoding="utf-8")
            self.assertIn("_image_companion_generate", companion)
            self.assertNotIn("reference_asset_ticket", companion)
        self.assertIn("_q5_structured_reference_asset_projection", page_api)
        self.assertIn("structuredReferenceAssetStatusHtml", frontend)


if __name__ == "__main__":
    unittest.main()
