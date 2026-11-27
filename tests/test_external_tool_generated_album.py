"""Tests for external LLM image-generation tool album capture, guardrails, and projection."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image as PILImage

from astrbot.core.message.components import Image as AstrImage, Plain
from astrbot.core.message.message_event_result import MessageChain

from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA,
    activate_core_reply_delivery_guard,
    prepare_core_reply_event,
)
from astrbot_host.core_reply_external_images import (
    EXTERNAL_META_ATTR,
    is_internal_tool,
    is_tier2_image_generation_tool,
    observe_external_cached_image,
    observe_external_media_send_returned,
    observe_external_tool_begin,
    observe_external_tool_end,
)
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.media.external_generated_album import (
    EXTERNAL_IMAGE_ACTION,
    EXTERNAL_IMAGE_PREFIX,
    backfill_external_generated_image,
    delete_external_generated_image,
    external_image_id,
    find_by_md5,
    has_duplicate_sha256,
    record_external_generated_image,
    validate_external_image_bytes,
)
from companion_v2.conversation.native_tool_results import PREFIX as NATIVE_PREFIX
from companion_v2.execution.models import TaskContext, TaskError, require
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.group_audience import group_audience
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.qzone.capability import PUBLISH_ACTION
from companion_v2.qzone.images import album_images as qzone_album_images, list_album_candidates
from companion_v2.references.configuration import (
    GROUP as REFERENCE_GROUP,
    ReferenceConfigurationOwner,
    configuration_fields as reference_fields,
)
from companion_v2.references.management import MANAGEMENT_ACTION, MANAGEMENT_SCOPE, ReferenceMaterialManagement
from companion_v2.storage.runtime import StorageRuntime
from tests.test_astrbot_core_reply import FakeEvent
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


def _make_static_image(color: tuple[int, int, int] = (40, 120, 200), *, size=(320, 256), fmt="PNG") -> bytes:
    buf = BytesIO()
    PILImage.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _make_animated_gif(*, size=(300, 300)) -> bytes:
    buf = BytesIO()
    f1 = PILImage.new("RGB", size, (255, 0, 0))
    f2 = PILImage.new("RGB", size, (0, 0, 255))
    f1.save(buf, format="GIF", save_all=True, append_images=[f2], duration=100, loop=0)
    return buf.getvalue()


def _make_animated_webp(*, size=(300, 300)) -> bytes:
    buf = BytesIO()
    f1 = PILImage.new("RGB", size, (255, 0, 0))
    f2 = PILImage.new("RGB", size, (0, 255, 0))
    f1.save(buf, format="WEBP", save_all=True, append_images=[f2], duration=100, loop=0)
    return buf.getvalue()


class ExternalToolClassifierTests(unittest.TestCase):
    def test_internal_and_blacklist_tools_are_rejected(self):
        self.assertTrue(is_internal_tool("companion_v2_image_generate"))
        self.assertTrue(is_internal_tool("companion_v2_reaction_search"))
        self.assertTrue(is_internal_tool("send_message_to_user"))
        self.assertTrue(is_internal_tool("astrbot_render_t2i"))

        blocked = (
            "companion_v2_image_generate",
            "companion_v2_reaction_search",
            "send_message_to_user",
            "astrbot_web_search",
            "browser_screenshot",
            "playwright_capture_screen",
            "web_image_search",
            "generate_qrcode",
            "send_sticker_pack",
            "meme_generator",
            "camera_snapshot",
            "render_mermaid_diagram",
            "t2i_card_render",
            "ocr_image_read",
            "fetch_user_avatar",
            "notify_user_status",
        )
        for name in blocked:
            with self.subTest(tool=name):
                self.assertFalse(
                    is_tier2_image_generation_tool(name, description="helper tool", tool_args={"prompt": "test"})
                )

    def test_third_party_generation_tools_pass_tier2(self):
        allowed = (
            ("NAI_Generate_Image", "Generate anime image", {"tag": "1girl, landscape"}),
            ("flux_generate", "Flux image model", {"prompt": "sunset over sea"}),
            ("comfyui_txt2img", "", {"prompt": "mountain"}),
            ("sd_draw_illustration", "", {}),
            ("custom_art_studio", "Create artwork picture", {"prompt": "starry sky"}),
        )
        for name, desc, args in allowed:
            with self.subTest(tool=name):
                self.assertTrue(is_tier2_image_generation_tool(name, description=desc, tool_args=args))


class ExternalAlbumValidationAndBackfillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="companion-ext-album-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.runtime = StorageRuntime.open(data_root=self.root, installation_id="test-ext-album")
        self.addCleanup(self.runtime.close)

    def test_image_quality_validation_enforces_static_format_and_dimensions(self):
        png = _make_static_image((12, 34, 56), size=(1216, 832), fmt="PNG")
        mime, w, h, sha, md5 = validate_external_image_bytes(png)
        self.assertEqual((mime, w, h), ("image/png", 1216, 832))
        self.assertEqual(sha, hashlib.sha256(png).hexdigest())
        self.assertEqual(md5, hashlib.md5(png).hexdigest())

        jpeg = _make_static_image((99, 88, 77), size=(300, 300), fmt="JPEG")
        self.assertEqual(validate_external_image_bytes(jpeg)[0], "image/jpeg")

        webp = _make_static_image((11, 22, 33), size=(256, 256), fmt="WEBP")
        self.assertEqual(validate_external_image_bytes(webp)[0], "image/webp")

        for bad_name, bad_bytes in (
            ("empty", b""),
            ("corrupt", b"not-an-image"),
            ("too_small", _make_static_image(size=(255, 512))),
            ("animated_gif", _make_animated_gif()),
            ("animated_webp", _make_animated_webp()),
        ):
            with self.subTest(case=bad_name):
                with self.assertRaises(ValueError):
                    validate_external_image_bytes(bad_bytes)

    def test_explicit_backfill_is_idempotent_and_validates_md5(self):
        origin = "aa11bb22" + "0" * 56
        png = _make_static_image((20, 40, 60), size=(640, 480))
        md5_hex = hashlib.md5(png).hexdigest()
        img_file = self.root / "explicit_source.png"
        img_file.write_bytes(png)

        with self.assertRaises(ValueError):
            backfill_external_generated_image(
                self.runtime,
                origin_key=origin,
                image_path=img_file,
                created_at="2026-11-27T15:00:39+08:00",
                expected_md5="0" * 32,
            )

        first = backfill_external_generated_image(
            self.runtime,
            origin_key=origin,
            image_path=img_file,
            created_at="2026-11-27T15:00:39+08:00",
            tool_name="NAI_Generate_Image",
            prompt="landscape evening sky",
            expected_md5=md5_hex,
        )
        self.assertEqual(first["status"], "recorded")
        self.assertTrue(first["image_id"].startswith(f"{EXTERNAL_IMAGE_PREFIX}aa11bb22:"))

        second = backfill_external_generated_image(
            self.runtime,
            origin_key=origin,
            image_path=img_file,
            created_at="2026-11-27T15:00:39+08:00",
            tool_name="NAI_Generate_Image",
            prompt="landscape evening sky",
            expected_md5=md5_hex,
        )
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["image_id"], first["image_id"])

        found = find_by_md5(self.runtime, (origin,), md5_hex)
        self.assertIsNotNone(found)
        self.assertEqual(found.content, png)
        self.assertIsNone(find_by_md5(self.runtime, ("ff99" + "1" * 60,), md5_hex))
        self.assertIsNone(find_by_md5(self.runtime, (origin,), md5_hex, hidden_ids={first["image_id"]}))

        self.assertTrue(delete_external_generated_image(self.runtime, (origin,), first["image_id"]))
        self.assertIsNone(find_by_md5(self.runtime, (origin,), md5_hex))

    def test_cross_ledger_sha256_dedup_against_native_core_tool_record(self):
        origin = "cc33dd44" + "0" * 56
        other_origin = "ee55ff66" + "0" * 56
        png = _make_static_image((70, 80, 90), size=(512, 512))
        sha_hex = hashlib.sha256(png).hexdigest()

        native_doc = {
            "invocation_id": "native-tool-existing",
            "origin_key": origin,
            "action": "image.generate",
            "state": "succeeded",
            "images": [{"position": 0, "content_hash": sha_hex}],
        }
        with self.runtime.transaction() as conn:
            conn.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES(?, ?)",
                (NATIVE_PREFIX + "native-tool-existing", json.dumps(native_doc)),
            )

        self.assertEqual(has_duplicate_sha256(self.runtime, (origin,), sha_hex), "generated-image:native-tool-existing:0")
        res = record_external_generated_image(self.runtime, origin_key=origin, content=png, created_at=NOW)
        self.assertEqual(res["status"], "duplicate")

        # Same bytes in a completely separate origin_key are NOT suppressed across scopes.
        other_res = record_external_generated_image(self.runtime, origin_key=other_origin, content=png, created_at=NOW)
        self.assertEqual(other_res["status"], "recorded")


class ExternalToolRealtimeCaptureTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = (await self.start(self.host())).application
        self.context = TaskContext(
            self.important,
            require(self.identity.resolve_role(SCOPE, observed_at=NOW)),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        self.submitted_jobs = []

    def _prepare_event(self, *, fail_send: bool = False):
        event = FakeEvent()

        async def original_send(message, *args, **kwargs):
            if fail_send:
                return False
            event.sent.append(message)
            return True

        event.send = original_send
        prepared = {"context": self.context, "event_id": "turn-ext-1"}

        async def authorize(value):
            return self.app.tasks.check_context_access(value["context"], action="conversation.reply", observed_at=NOW)

        def submit(fn):
            self.submitted_jobs.append(fn)
            return True

        prepare_core_reply_event(
            event,
            prepared,
            authorize=authorize,
            media_submit=submit,
            application=self.app,
        )
        return event

    def _drain(self):
        while self.submitted_jobs:
            job = self.submitted_jobs.pop(0)
            job()

    async def test_path1_tier1_direct_send_captures_with_metadata_and_respects_guardrails(self):
        event = self._prepare_event()
        png = _make_static_image((12, 80, 140), size=(1216, 832))
        chain = MessageChain([AstrImage.fromBase64(base64.b64encode(png).decode("ascii"))])
        setattr(
            chain,
            EXTERNAL_META_ATTR,
            {
                "prompt": "quiet winter street at dusk",
                "model": "nai-diffusion-4",
                "size": "1216x832",
                "source_plugin": "astrbot_plugin_nai_image",
            },
        )

        # Outside active tool window -> must NOT capture.
        await event.send(chain)
        self._drain()
        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 0)

        # Inside active external tool window -> captures asynchronously via media_submit.
        tool = NS(name="NAI_Generate_Image", description="Generate anime illustration")
        observe_external_tool_begin(event, tool, {"tag": "fallback tag"})
        await event.send(chain)
        observe_external_tool_end(event, tool, {}, "sent")
        self.assertEqual(len(self.submitted_jobs), 1)
        self._drain()

        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 1)
        page = self.app.messages.generated_images.album_page(self.context, NOW, limit=10)
        item = page["items"][0]
        self.assertEqual(item.action, EXTERNAL_IMAGE_ACTION)
        self.assertEqual((item.descriptor.width, item.descriptor.height), (1216, 832))
        self.assertEqual(item.content, png)
        self.assertEqual(
            self.app.messages.generated_images.album_prompt_excerpt(self.context, NOW, item.image_id),
            "quiet winter street at dusk",
        )

        # Duplicate send of same bytes in same scope -> deduplicated, count stays 1.
        observe_external_tool_begin(event, tool, {})
        await event.send(chain)
        observe_external_tool_end(event, tool, {}, "sent")
        self._drain()
        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 1)

    async def test_path1_blocks_failed_send_guarded_t2i_blacklist_and_bad_images(self):
        # 1) Platform send returns False -> must NOT record.
        fail_event = self._prepare_event(fail_send=True)
        png1 = _make_static_image((200, 10, 10), size=(512, 512))
        tool = NS(name="flux_generate", description="Generate image")
        observe_external_tool_begin(fail_event, tool, {"prompt": "red cube"})
        await fail_event.send(MessageChain([AstrImage.fromBase64(base64.b64encode(png1).decode("ascii"))]))
        observe_external_tool_end(fail_event, tool, {}, "failed")
        self._drain()
        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 0)

        # 2) Blacklisted screenshot tool -> must NOT record.
        event = self._prepare_event()
        shot_tool = NS(name="browser_screenshot", description="Take webpage screenshot")
        observe_external_tool_begin(event, shot_tool, {"url": "https://example.com"})
        await event.send(MessageChain([AstrImage.fromBase64(base64.b64encode(png1).decode("ascii"))]))
        observe_external_tool_end(event, shot_tool, {}, "ok")
        self._drain()
        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 0)

        # 3) Animated GIF & tiny 128x128 icon from eligible tool -> rejected by Guardrail 3.
        observe_external_tool_begin(event, tool, {"prompt": "tiny or animated"})
        tiny = _make_static_image((10, 20, 30), size=(128, 128))
        gif = _make_animated_gif()
        await event.send(MessageChain([
            AstrImage.fromBase64(base64.b64encode(tiny).decode("ascii")),
            AstrImage.fromBase64(base64.b64encode(gif).decode("ascii")),
        ]))
        observe_external_tool_end(event, tool, {}, "ok")
        self._drain()
        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 0)

        # 4) Entering guarded reply delivery (T2I / main reply / deferred stickers) clears active tools & blocks capture.
        observe_external_tool_begin(event, tool, {"prompt": "stale tool window"})
        activate_core_reply_delivery_guard(event)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertTrue(state["delivery_guard_active"])
        self.assertEqual(state["_external_image_capture"]["active"], [])

    async def test_path2_cached_image_via_send_message_to_user_captures_only_eligible_external_tools(self):
        event = self._prepare_event()
        png = _make_static_image((33, 180, 99), size=(512, 384))
        cached_file = self.root / "mcp_flux_out.png"
        cached_file.write_bytes(png)

        tool = NS(name="mcp_flux_generate", description="Generate image via MCP")
        observe_external_tool_begin(event, tool, {"prompt": "green forest sunlight"})
        observe_external_tool_end(event, tool, {}, "ok")

        # Internal reaction_search and blacklisted screenshot cached images must be ignored.
        self.assertFalse(observe_external_cached_image(event, NS(tool_name="companion_v2_reaction_search", file_path=str(cached_file))))
        self.assertFalse(observe_external_cached_image(event, NS(tool_name="browser_screenshot", file_path=str(cached_file))))

        # Eligible external tool cached image is tracked even after tool_end (via recent_tools).
        self.assertTrue(observe_external_cached_image(event, NS(tool_name="mcp_flux_generate", file_path=str(cached_file))))

        sent_chain = MessageChain([AstrImage.fromFileSystem(str(cached_file)), Plain("sent")])
        self.assertTrue(
            observe_external_media_send_returned(
                event, sent_chain, submit=lambda fn: self.submitted_jobs.append(fn) or True,
            )
        )
        self._drain()

        self.assertEqual(self.app.messages.generated_images.album_count(self.context, NOW), 1)
        page = self.app.messages.generated_images.album_page(self.context, NOW, limit=10)
        self.assertEqual(page["items"][0].content, png)
        self.assertEqual(
            self.app.messages.generated_images.album_prompt_excerpt(self.context, NOW, page["items"][0].image_id),
            "green forest sunlight",
        )


class ExternalAlbumUnifiedProjectionAndQZoneTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = (await self.start(self.host())).application
        self.context = TaskContext(
            self.important,
            require(self.identity.resolve_role(SCOPE, observed_at=NOW)),
            AudienceRef(SCOPE, (self.important.subject,)),
        )
        # Grant management permission to core operator for ReferenceMaterialManagement
        mgmt_scope = ScopeRef(self.runtime.installation_id, "companion", ScopeKind.SYSTEM, MANAGEMENT_SCOPE)
        self.grant(self.core, mgmt_scope, MANAGEMENT_ACTION)
        # Enable photo references on role for register_from_album test
        ref_owner = ReferenceConfigurationOwner(self.runtime)
        ref_target = self.app.reference_settings.target(self.context)
        ref_owner.write(
            ref_target,
            reference_fields(),
            changes={f"{REFERENCE_GROUP}.enabled": True},
            reset=(),
            expected_revision=ref_owner.read(ref_target, reference_fields()).revision,
            observed_at=NOW,
        )

        # Build a second private peer context and a group context for isolation checks.
        self.other_scope = replace(SCOPE, native_scope_id="peer-other", session_id="peer-other", conversation_id="conv-other")
        other_subject = SubjectRef(SCOPE.installation_id, SubjectKind.HUMAN, "other-human")
        other_principal = replace(self.important.principal, native_account_id="peer-other")
        self.identity.bind(
            binding_id="other-bind", principal=other_principal, subject=other_subject,
            valid_from=NOW, expected_revision=self.identity.revision,
        )
        self.policy.set_tier(other_subject, AuthorizationTier.STANDARD, expected_revision=self.policy.revision, observed_at=NOW)
        self.personas.register_route(ConversationRoute(self.other_scope, user=other_subject), observed_at=NOW)
        other_actor = require(self.identity.resolve_authenticated(other_principal, observed_at=NOW))
        for action in ("text", "conversation.reply"):
            self.grant(other_actor, self.other_scope, action)
        self.other_context = TaskContext(
            other_actor,
            require(self.identity.resolve_role(self.other_scope, observed_at=NOW)),
            AudienceRef(self.other_scope, (other_subject,)),
        )

        self.group_scope = replace(SCOPE, kind=ScopeKind.GROUP, native_scope_id="grp-1", session_id="grp-1", conversation_id="conv-grp")
        self.personas.register_route(ConversationRoute(self.group_scope, group_id="grp-1"), observed_at=NOW)
        for action in ("text", "conversation.reply"):
            self.grant(self.important, self.group_scope, action)
        self.group_context = TaskContext(
            self.important,
            require(self.identity.resolve_role(self.group_scope, observed_at=NOW)),
            group_audience(self.group_scope, self.important.subject),
        )

    async def test_unified_pagination_isolation_management_actions_and_qzone(self):
        # Record 3 images for self.context (2 sharing identical created_at to test tie-breaker cursor!)
        t1 = NOW + timedelta(minutes=1)
        t2 = NOW + timedelta(minutes=2)
        img_a1 = _make_static_image((10, 20, 30), size=(640, 480))
        img_a2 = _make_static_image((40, 50, 60), size=(1216, 832))
        img_a3 = _make_static_image((70, 80, 90), size=(1216, 832))
        img_other = _make_static_image((200, 100, 50), size=(512, 512))
        img_grp = _make_static_image((50, 200, 100), size=(512, 512))

        r1 = record_external_generated_image(self.runtime, origin_key=self.context.key, content=img_a1, created_at=t1, tool_name="NAI_Generate_Image", prompt="first scene")
        r2 = record_external_generated_image(self.runtime, origin_key=self.context.key, content=img_a2, created_at=t2, tool_name="NAI_Generate_Image", prompt="second scene")
        r3 = record_external_generated_image(self.runtime, origin_key=self.context.key, content=img_a3, created_at=t2, tool_name="NAI_Generate_Image", prompt="third scene same second")
        r_other = record_external_generated_image(self.runtime, origin_key=self.other_context.key, content=img_other, created_at=t2, tool_name="NAI_Generate_Image", prompt="other user private")
        r_grp = record_external_generated_image(self.runtime, origin_key=self.group_context.key, content=img_grp, created_at=t2, tool_name="NAI_Generate_Image", prompt="group image")

        generated = self.app.messages.generated_images

        # 1) Strict origin_key / peer / group isolation
        self.assertEqual(generated.album_count(self.context, NOW), 3)
        self.assertEqual(generated.album_count(self.other_context, NOW), 1)
        self.assertEqual(generated.album_count(self.group_context, NOW), 1)
        with self.assertRaises(TaskError) as cross_err:
            generated.album_image(self.other_context, NOW, r1["image_id"])
        self.assertEqual(cross_err.exception.code, "generated_image_missing")
        with self.assertRaises(TaskError) as grp_err:
            generated.album_image(self.context, NOW, r_grp["image_id"])
        self.assertEqual(grp_err.exception.code, "generated_image_missing")
        self.assertEqual(generated.album_image(self.other_context, NOW, r_other["image_id"]).content, img_other)

        # 2) Cursor pagination with limit=2 across equal-timestamp external items loses ZERO items
        collected_ids = []
        cursor = None
        for _ in range(5):
            page = generated.album_page(self.context, NOW, limit=2, cursor=cursor)
            collected_ids.extend(item.image_id for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(collected_ids), 3)
        self.assertEqual(set(collected_ids), {r1["image_id"], r2["image_id"], r3["image_id"]})
        self.assertEqual(collected_ids[-1], r1["image_id"])  # oldest last

        # 3) ReferenceMaterialManagement: album, album_image, export_album, open_album_folder, register_from_album, hide
        mgmt = ReferenceMaterialManagement(self.app)
        album_res = mgmt.album(self.core, self.context, at=NOW, limit=10)
        self.assertEqual(album_res["total"], 3)
        self.assertEqual(len(album_res["items"]), 3)
        self.assertTrue(all(entry["thumbnail"].startswith("data:image/jpeg;base64,") for entry in album_res["items"]))

        full_img = mgmt.album_image(self.core, self.context, at=NOW, image_id=r2["image_id"])
        self.assertEqual((full_img["width"], full_img["height"]), (1216, 832))
        self.assertTrue(full_img["data_url"].startswith("data:image/png;base64,"))

        exported = mgmt.export_album(self.core, self.context, at=NOW, image_ids=[r1["image_id"], r2["image_id"]])
        self.assertEqual(exported["count"], 2)
        zip_bytes = base64.b64decode(exported["data_url"].split(",", 1)[1])
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            self.assertEqual(len(zf.namelist()), 2)
            self.assertEqual(zf.read(zf.namelist()[1]), img_a2)

        with patch("os.startfile", create=True), patch("subprocess.Popen"):
            opened = mgmt.open_album_folder(self.core, self.context, at=NOW)
        self.assertEqual(opened["total_count"], 3)
        self.assertEqual(opened["synced_count"], 3)

        registered = mgmt.register_from_album(
            self.core, self.context, at=NOW,
            image_id=r2["image_id"], label="横版参考", roles=["scene"], operation_key="ext-ref-op-key-0001",
        )
        self.assertEqual(registered["label"], "横版参考")

        # Hide one external image -> count drops to 2, album_image refuses it, open_album_folder sees 2
        mgmt.hide_album_images(self.core, self.context, at=NOW, image_ids=[r3["image_id"]])
        album_after_hide = mgmt.album(self.core, self.context, at=NOW, limit=10)
        self.assertEqual(album_after_hide["total"], 2)
        self.assertNotIn(r3["image_id"], [e["image_id"] for e in album_after_hide["items"]])
        with self.assertRaises(TaskError):
            mgmt.album_image(self.core, self.context, at=NOW, image_id=r3["image_id"])

        # 4) QZone integration: candidates + PUBLISH_ACTION auth gate
        candidates = list_album_candidates(self.app, self.context, NOW, limit=10)
        candidate_ids = [c["image_id"] for c in candidates]
        self.assertIn(r2["image_id"], candidate_ids)
        self.assertNotIn(r3["image_id"], candidate_ids)
        r2_entry = next(c for c in candidates if c["image_id"] == r2["image_id"])
        self.assertEqual(r2_entry["summary"], "second scene")

        # Without PUBLISH_ACTION grant -> denied!
        with self.assertRaises(TaskError):
            qzone_album_images(self.app, self.context, NOW, [r2["image_id"]])

        # With PUBLISH_ACTION grant -> authorized and hash-verified!
        self.grant(self.important, SCOPE, PUBLISH_ACTION)
        pub_images, pub_ids = qzone_album_images(self.app, self.context, NOW, [r2["image_id"]])
        self.assertEqual(len(pub_images), 1)
        self.assertEqual(pub_images[0].content, img_a2)
        self.assertEqual(pub_ids, (r2["image_id"],))


if __name__ == "__main__":
    unittest.main()
