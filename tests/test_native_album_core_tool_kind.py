"""Regression tests for stable & legacy core-tool TaskDefinition kinds in native album projection."""

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
import unittest
from unittest.mock import patch

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.external_tools import core_tool_kind
from companion_v2.conversation.media import native_generated_album
from companion_v2.conversation.native_tool_results import (
    PREFIX,
    match_core_tool_definition,
)
from companion_v2.execution.models import (
    Lane, ProviderBinding, SideEffect, TaskDefinition, TaskError, digest, encode,
)
from companion_v2.references.management import ReferenceMaterialManagement
from companion_v2.storage.generated_album import role_album_dir
from tests import test_core_reply_images as _core_reply_images_mod


@dataclass(frozen=True)
class _StubTool:
    action: str


@dataclass(frozen=True)
class _StubDescriptor:
    tool: _StubTool
    task_def: TaskDefinition

    def definition(self):
        return self.task_def


class CoreToolDefinitionMatcherTests(unittest.TestCase):
    def test_match_core_tool_definition_accepts_stable_and_legacy_and_rejects_tampered(self):
        task_def = TaskDefinition(
            kind="image.generate",
            owner="image.native",
            action="image.generate",
            lane=Lane.FOREGROUND,
            side_effect=SideEffect.EXTERNAL_NETWORK,
            timeout_seconds=1800,
            binding=ProviderBinding(
                provider="image.native",
                capability="image.generate",
                version="7.3.5",
                generation="gen-1",
            ),
        )
        descriptor = _StubDescriptor(tool=_StubTool("image.generate"), task_def=task_def)
        stable_def = replace(task_def, kind=core_tool_kind("image.generate"), owner="astrbot.core-tool")
        legacy_def = replace(task_def, kind=PREFIX + digest(descriptor), owner="astrbot.core-tool")
        tampered_def = replace(task_def, kind="astrbot.core-tool.forged", owner="astrbot.core-tool")

        self.assertEqual(
            match_core_tool_definition(descriptor, json.loads(encode(stable_def))),
            stable_def,
        )
        self.assertEqual(
            match_core_tool_definition(descriptor, json.loads(encode(legacy_def))),
            legacy_def,
        )
        self.assertIsNone(match_core_tool_definition(descriptor, json.loads(encode(tampered_def))))
        self.assertIsNone(match_core_tool_definition(descriptor, None))


class _CoreReplyImageHarness(_core_reply_images_mod.CoreReplyImagesTests):
    """Reuse CoreReplyImagesTests fixture without re-running its unrelated test methods."""


for _attr in [name for name in dir(_CoreReplyImageHarness) if name.startswith("test_")]:
    setattr(_CoreReplyImageHarness, _attr, None)


class NativeGeneratedAlbumKindCompatTests(_CoreReplyImageHarness):
    async def test_stable_and_legacy_core_tool_kinds_stay_consistent_across_album_and_folder_sync(self):
        event, request = self.prepare("album-kind-compat")
        gen1 = self.result(
            await self.call(event, request, "image.generate", {"prompt": "红色"}, "gen-stable"),
            "red",
        )
        art1 = json.loads(gen1["payload"])["native"]["artifacts"][0]["artifact_id"]
        edit1 = self.result(
            await self.call(
                event, request, "image.edit", {"prompt": "蓝色", "artifact_id": art1}, "edit-stable",
            ),
            "blue",
        )
        art2 = json.loads(edit1["payload"])["native"]["artifacts"][0]["artifact_id"]
        gen_legacy = self.result(
            await self.call(event, request, "image.generate", {"prompt": "红二"}, "gen-legacy"),
            "red",
        )
        art_legacy = json.loads(gen_legacy["payload"])["native"]["artifacts"][0]["artifact_id"]

        inv_stable_gen = gen1["invocation_id"]
        inv_stable_edit = edit1["invocation_id"]
        inv_legacy = gen_legacy["invocation_id"]

        def read_task_record(inv_id):
            op = native_generated_album._operation_or_none(self.runtime, inv_id)
            row = self.runtime.read_one(
                "SELECT task_id, record_json FROM execution_tasks WHERE task_id = ?",
                (op["task_id"],),
            )
            return op, row["task_id"], json.loads(row["record_json"])

        op_gen, _, rec_gen = read_task_record(inv_stable_gen)
        op_edit, _, rec_edit = read_task_record(inv_stable_edit)
        self.assertEqual(rec_gen["definition"]["kind"], core_tool_kind("image.generate"))
        self.assertEqual(rec_gen["spec"]["kind"], core_tool_kind("image.generate"))
        self.assertEqual(rec_edit["definition"]["kind"], core_tool_kind("image.edit"))
        self.assertEqual(rec_edit["spec"]["kind"], core_tool_kind("image.edit"))

        # Rewrite the third operation's execution_tasks row to pre-migration legacy hash kind.
        op_leg, leg_task_id, rec_leg = read_task_record(inv_legacy)
        orig_desc, _ = native_generated_album._album_descriptors(self.app.messages.tools.external, op_leg)
        legacy_kind = PREFIX + digest(orig_desc)
        rec_leg["definition"]["kind"] = legacy_kind
        rec_leg["spec"]["kind"] = legacy_kind
        with self.runtime.transaction() as conn:
            conn.execute(
                "UPDATE execution_tasks SET kind = ?, record_json = ? WHERE task_id = ?",
                (legacy_kind, encode(rec_leg), leg_task_id),
            )

        now = self.clock.now()
        generated = self.app.messages.generated_images

        # 1. count() and page() match exactly (3 == 3), covering both stable & legacy kinds.
        counted, counted_ids = native_generated_album.count(generated, self.context)
        self.assertEqual(counted, 3)
        self.assertEqual(generated.album_count(self.context, now), 3)

        page_items, has_more = native_generated_album.page(generated, self.context, now, limit=24)
        self.assertFalse(has_more)
        self.assertEqual(len(page_items), 3)
        self.assertEqual({item.image_id for item in page_items}, counted_ids)

        # 2. image() and artifact() resolve BOTH stable and legacy items.
        for inv_id, art_id in (
            (inv_stable_gen, art1),
            (inv_stable_edit, art2),
            (inv_legacy, art_legacy),
        ):
            img_item = native_generated_album.image(generated, self.context, now, inv_id, 0)
            art_item = native_generated_album.artifact(generated, self.context, now, art_id)
            self.assertEqual(img_item.image_id, f"generated-image:{inv_id}:0")
            self.assertEqual(art_item.image_id, img_item.image_id)
            self.assertTrue(len(img_item.content) > 0)

        # 3. ReferenceMaterialManagement.album() & open_album_folder() agree (loaded == total == synced == 3).
        manager = ReferenceMaterialManagement(self.app)
        operator = dashboard_operator(self.runtime, "album-kind-admin", at=now)
        album_resp = manager.album(operator, self.context, at=now, limit=24)
        self.assertEqual(album_resp["total"], 3)
        self.assertEqual(len(album_resp["items"]), 3)
        self.assertIsNone(album_resp["next_cursor"])

        opened = []
        launcher_attr = "os.startfile" if os.name == "nt" else "subprocess.Popen"

        def fake_launch(arg, *_args, **_kwargs):
            opened.append(arg if isinstance(arg, str) else arg[-1])

        with patch(f"companion_v2.references.management.{launcher_attr}", side_effect=fake_launch):
            folder_resp = manager.open_album_folder(operator, self.context, at=now)

        self.assertEqual(folder_resp["synced_count"], 3)
        self.assertEqual(folder_resp["total_count"], 3)
        expected_dir = role_album_dir(
            Path(self.runtime.data_root).resolve(),
            self.context.role_binding.role.subject_id,
            owner=self.app.references.owner(self.context),
        )
        self.assertEqual(len(list(expected_dir.iterdir())), 3)

        # 4. Tampered kind (neither stable nor legacy) must still be strictly rejected.
        rec_leg["definition"]["kind"] = "astrbot.core-tool.tampered"
        rec_leg["spec"]["kind"] = "astrbot.core-tool.tampered"
        with self.runtime.transaction() as conn:
            conn.execute(
                "UPDATE execution_tasks SET kind = ?, record_json = ? WHERE task_id = ?",
                ("astrbot.core-tool.tampered", encode(rec_leg), leg_task_id),
            )
        after_tamper, _ = native_generated_album.page(generated, self.context, now, limit=24)
        self.assertEqual(len(after_tamper), 2)
        with self.assertRaises(TaskError) as err:
            native_generated_album.image(generated, self.context, now, inv_legacy, 0)
        self.assertEqual(err.exception.code, "generated_image_missing")


if __name__ == "__main__":
    unittest.main()
