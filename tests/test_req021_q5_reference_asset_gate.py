from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
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

        for generation_id, backend in (("other", "comfyui"), ("gen-2", "other")):
            denied, denied_status = self.gate.consume(
                ticket,
                generation_id=generation_id,
                backend=backend,
                capacity=1,
            )
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

    def test_expired_ticket_cannot_disclose_managed_asset_path(self) -> None:
        now = [1000.0]
        gate = ReferenceAssetGate(self.data_dir, now=lambda: now[0])
        identity = self._entry("identity-expiring.png", "identity")
        plan, status = gate.plan(
            [identity], generation_id="expiring-generation", mode="new_topic"
        )
        self.assertEqual("ok", status)
        ticket = gate.issue(plan, backend="external")
        self.assertIsNotNone(ticket)

        now[0] += 90.0
        paths, consumed = gate.consume(
            ticket,
            generation_id="expiring-generation",
            backend="external",
            capacity=1,
        )
        self.assertEqual([], paths)
        self.assertEqual("expired_or_consumed_ticket", consumed)
