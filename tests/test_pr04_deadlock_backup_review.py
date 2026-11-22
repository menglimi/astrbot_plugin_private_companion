"""Regression tests for configuration cross-store locking, full backup coverage, and group image review."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from companion_v2.capabilities import CapabilityPackageDescriptor, FurnitureDescriptor
from companion_v2.capabilities.configuration import CapabilityConfigurationOwner
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.configuration.management import DiaryAndRestManagement, PrivateAcquaintanceManagement, RoleGrowthManagement
from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.store import open_configuration_store
from companion_v2.conversation.media.review import KIND as REVIEW_KIND, check_verdict, verdict_document
from companion_v2.conversation.media.review_service import GroupImageReviewService
from companion_v2.conversation.participation import (
    CONFIG_GROUP as PARTICIPATION_GROUP,
    GroupParticipationConfigurationOwner,
    configuration_fields as participation_fields,
)
from companion_v2.conversation.recall_settings import (
    GROUP as RECALL_GROUP,
    RecallConfigurationOwner,
    configuration_fields as recall_fields,
)
from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.refs import OperationRef, ScopeKind, ScopeRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.model_gateway.pricing import PricingSettingsStore
from companion_v2.relationships.private_management import PrivateMemberSettings
from companion_v2.role_work.growth.configuration import (
    GROUP as GROWTH_GROUP,
    OWNER as GROWTH_OWNER,
    target_for as growth_target_for,
)
from companion_v2.role_work.state.configuration import (
    GROUP as STATE_GROUP,
    OWNER as STATE_OWNER,
    target_for as state_target_for,
)
from companion_v2.storage.backup import InstallationBackup, apply_pending, current_schema_version
from companion_v2.storage.image_payloads import load_image, store_image
from companion_v2.storage.runtime import StorageRuntime
from astrbot_host.management_identity import dashboard_operator
from tests.test_conversation_messages import MessageFixture
from tests.test_management_access import NOW as MSG_NOW
from tests.multi_host_support import MultiFixture


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
INSTALLATION_ID = "pr04-test-installation"
SAMPLE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


class SplitStoreAtomicityTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="pr04-split-store-")
        self.addCleanup(temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(temporary.name) / "data", installation_id=INSTALLATION_ID)
        self.addCleanup(self.runtime.close)
        self.store = open_configuration_store(self.runtime)
        self.assertIsNotNone(self.store)
        self.assertIsNot(self.store, self.runtime)

    def test_pure_config_owner_validation_failure_rolls_back_side_store_without_locking_main_runtime(self):
        scope = ScopeRef(INSTALLATION_ID, "onebot", ScopeKind.GROUP, "group-1", "bot-a", "acc-a", "conv-1", "sess-1")
        role = SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a")
        target = ConfigTarget(INSTALLATION_ID, ConfigLayer.WINDOW, role, scope)
        owner = GroupParticipationConfigurationOwner(self.runtime)
        fields = participation_fields()

        main_tx_calls = 0
        original_main_tx = self.runtime.transaction

        @contextmanager
        def tracking_main_tx(*args, **kwargs):
            nonlocal main_tx_calls
            main_tx_calls += 1
            with original_main_tx(*args, **kwargs) as conn:
                yield conn

        with patch.object(self.runtime, "transaction", side_effect=tracking_main_tx):
            # First write a valid setting; main runtime transaction must not be acquired.
            written = owner.write(
                target, fields,
                changes={PARTICIPATION_GROUP + ".group_repeat_interrupt_text": "先别刷屏"},
                reset=(), expected_revision=0, observed_at=NOW,
            )
            self.assertEqual(written.revision, 1)
            self.assertEqual(main_tx_calls, 0)

            # Now write an invalid control-character value that fails post-write validation.
            # Because GroupParticipationConfigurationOwner uses self.store.transaction(),
            # the side store must roll back completely rather than persisting invalid state.
            with self.assertRaises(ConfigurationError):
                owner.write(
                    target, fields,
                    changes={PARTICIPATION_GROUP + ".group_repeat_interrupt_text": "bad\nline"},
                    reset=(), expected_revision=written.revision, observed_at=NOW + timedelta(seconds=1),
                )
            self.assertEqual(main_tx_calls, 0)

        snapshot = owner.read(target, fields)
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(snapshot.fields[PARTICIPATION_GROUP + ".group_repeat_interrupt_text"].value, "先别刷屏")

    def test_recall_owner_writes_atomically_on_side_store_without_locking_main_runtime(self):
        scope = ScopeRef(INSTALLATION_ID, "onebot", ScopeKind.PRIVATE, "u-1", "bot-a", "acc-a", "conv-1", "sess-1")
        role = SubjectRef(INSTALLATION_ID, SubjectKind.BOT, "role-a")
        target = ConfigTarget(INSTALLATION_ID, ConfigLayer.WINDOW, role, scope)
        owner = RecallConfigurationOwner(self.runtime)
        fields = recall_fields()

        main_tx_calls = 0
        original_main_tx = self.runtime.transaction

        @contextmanager
        def tracking_main_tx(*args, **kwargs):
            nonlocal main_tx_calls
            main_tx_calls += 1
            with original_main_tx(*args, **kwargs) as conn:
                yield conn

        with patch.object(self.runtime, "transaction", side_effect=tracking_main_tx):
            written = owner.write(
                target, fields,
                changes={RECALL_GROUP + ".enabled": True},
                reset=(), expected_revision=0, observed_at=NOW,
            )
            self.assertEqual(written.revision, 1)
            self.assertEqual(main_tx_calls, 0)


class CrossStoreLockOrderAndConcurrencyTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def _setup_private_context(self):
        app = await self.start_host()
        await self.controller.receive_claimed(self.event("first", "hello"))
        self.assertIsNot(app.configuration.store, app.runtime)
        context = self.controller.authority.context("first", "role-a")
        operator = dashboard_operator(app.runtime, "lock-admin", at=self.controller.now())
        return app, context, operator

    async def test_global_runtime_before_store_lock_order_holds_across_all_write_paths(self):
        app, context, operator = await self._setup_private_context()
        runtime = app.runtime
        store = app.configuration.store

        thread_state = threading.local()
        inversions: list[tuple[str, str]] = []
        original_runtime_tx = runtime.transaction
        original_store_tx = store.transaction

        def stack():
            items = getattr(thread_state, "held", None)
            if items is None:
                items = []
                thread_state.held = items
            return items

        @contextmanager
        def tracked_runtime_tx(*args, **kwargs):
            held = stack()
            if "store" in held and "runtime" not in held:
                inversions.append(("store", "runtime"))
            held.append("runtime")
            try:
                with original_runtime_tx(*args, **kwargs) as conn:
                    yield conn
            finally:
                held.pop()

        @contextmanager
        def tracked_store_tx(*args, **kwargs):
            held = stack()
            held.append("store")
            try:
                with original_store_tx(*args, **kwargs) as conn:
                    yield conn
            finally:
                held.pop()

        with (
            patch.object(runtime, "transaction", side_effect=tracked_runtime_tx),
            patch.object(store, "transaction", side_effect=tracked_store_tx),
        ):
            # 1) PrivateMemberSettings.configure (touches multiple sections including growth + person)
            member = PrivateMemberSettings(app, context)
            status = member.status(operator).unwrap()
            member_res = member.configure(operator, {
                "operation_key": "lock-order-member-1",
                "sections": {
                    "growth": {
                        "edit_token": status["sections"]["growth"]["edit_token"],
                        "changes": {"read_enabled": True},
                    },
                    "person": {
                        "edit_token": status["sections"]["person"]["edit_token"],
                        "changes": {"acquaintance_enabled": False},
                    },
                },
            })
            self.assertTrue(member_res.is_success, member_res.failure)

            # 2) PrivateAcquaintanceManagement.configure directly
            acq = PrivateAcquaintanceManagement(app, context)
            acq_status = acq.status(operator).unwrap()
            acq_res = acq.configure(operator, {
                "operation_key": "lock-order-acq-1",
                "edit_token": acq_status["edit_token"],
                "changes": {"acquaintance_enabled": True},
            })
            self.assertTrue(acq_res.is_success, acq_res.failure)

            # 3) Direct ConfigurationService.patch on GrowthConfigurationOwner (writes_main_runtime=True)
            g_target = growth_target_for(context)
            g_snap = app.configuration.read(operator, target=g_target, group_id=GROWTH_GROUP,
                                            observed_at=app.now()).unwrap()
            g_op = OperationRef("direct-growth-op-1", GROWTH_OWNER, g_target.authorization_scope,
                                VersionRef("direct-growth-op-1", 1), "direct-growth-op-1")
            g_patch = app.configuration.patch(
                operator, target=g_target, group_id=GROWTH_GROUP, operation=g_op,
                expected_edit_token=g_snap.edit_token,
                changes={GROWTH_GROUP + ".read_enabled": False},
                observed_at=app.now(),
            )
            self.assertTrue(g_patch.is_success, g_patch.failure)

            # 4) Direct ConfigurationService.patch on StateConfigurationOwner (writes_main_runtime=True)
            s_target = state_target_for(context)
            s_snap = app.configuration.read(operator, target=s_target, group_id=STATE_GROUP,
                                            observed_at=app.now()).unwrap()
            s_op = OperationRef("direct-state-op-1", STATE_OWNER, s_target.authorization_scope,
                                VersionRef("direct-state-op-1", 1), "direct-state-op-1")
            s_patch = app.configuration.patch(
                operator, target=s_target, group_id=STATE_GROUP, operation=s_op,
                expected_edit_token=s_snap.edit_token,
                changes={STATE_GROUP + ".typing_enabled": True},
                observed_at=app.now(),
            )
            self.assertTrue(s_patch.is_success, s_patch.failure)

            # 5) Direct ConfigurationService.patch on CapabilityConfigurationOwner (store_independent=True)
            cap_repo = CapabilityRepository(runtime)
            ext_id = "companion.pr04.lockcheck"
            selected_desc = CapabilityPackageDescriptor(
                extension_id=ext_id, package_id="companion.pr04.lockcheck-pkg", publisher_id="companion",
                package_version="1.0.0", artifact_hash="pr04-lockcheck-hash",
                capabilities=("pr04.read",), dataset_owners=("pr04",),
                furniture=(FurnitureDescriptor("pr04-panel", ext_id, "PR04", "pr04-panel"),),
            )
            cap_repo.register_descriptor(selected_desc, registered_at=app.now()).unwrap()
            cap_repo.acquire(
                extension_id=ext_id, package_id=selected_desc.package_id,
                package_version=selected_desc.package_version, expected_revision=0, observed_at=app.now(),
            ).unwrap()
            cap_owner = CapabilityConfigurationOwner(runtime, extension_id=ext_id)
            from companion_v2.configuration.registry import FieldRegistry
            from companion_v2.configuration.service import ConfigurationService
            cap_svc = ConfigurationService(
                runtime, FieldRegistry(version="capability-config.v1", fields=cap_owner.fields),
                store=store, owners=(cap_owner,),
            )
            cap_target = ConfigTarget(context.scope.installation_id, ConfigLayer.SCOPE, scope=context.scope)
            cap_snap = cap_svc.read(operator, target=cap_target, group_id=cap_owner.group_id,
                                    observed_at=app.now()).unwrap()
            cap_op = OperationRef("direct-cap-op-1", cap_owner.owner_id, cap_target.authorization_scope,
                                  VersionRef("direct-cap-op-1", 1), "direct-cap-op-1")
            cap_patch = cap_svc.patch(
                operator, target=cap_target, group_id=cap_owner.group_id, operation=cap_op,
                expected_edit_token=cap_snap.edit_token,
                changes={cap_owner.enabled_key: True},
                observed_at=app.now(),
            )
            self.assertTrue(cap_patch.is_success, cap_patch.failure)

        self.assertEqual(inversions, [], f"Detected store -> runtime lock inversions: {inversions}")

    async def test_concurrent_member_settings_and_group_editors_never_deadlock(self):
        app, context, operator = await self._setup_private_context()
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker_member():
            try:
                barrier.wait(timeout=5.0)
                member = PrivateMemberSettings(app, context)
                for idx in range(12):
                    snap = member.status(operator).unwrap()
                    res = member.configure(operator, {
                        "operation_key": f"stress-member-{idx}",
                        "sections": {
                            "person": {
                                "edit_token": snap["sections"]["person"]["edit_token"],
                                "changes": {"acquaintance_enabled": bool(idx % 2)},
                            },
                        },
                    })
                    if not res.is_success and res.failure.code != "configuration_revision_conflict":
                        raise AssertionError(f"unexpected member configure failure: {res.failure}")
            except BaseException as exc:
                errors.append(exc)

        def worker_state_editor():
            try:
                barrier.wait(timeout=5.0)
                diary = DiaryAndRestManagement(app, context)
                growth = RoleGrowthManagement(app, context)
                for idx in range(12):
                    d_snap = diary.status(operator).unwrap()
                    d_res = diary.configure(operator, {
                        "operation_key": f"stress-diary-{idx}",
                        "edit_token": d_snap["edit_token"],
                        "changes": {"enabled": bool(idx % 2)},
                    })
                    if not d_res.is_success and d_res.failure.code != "configuration_revision_conflict":
                        raise AssertionError(f"unexpected diary configure failure: {d_res.failure}")
                    g_snap = growth.status(operator).unwrap()
                    g_res = growth.configure(operator, {
                        "operation_key": f"stress-growth-{idx}",
                        "edit_token": g_snap["edit_token"],
                        "changes": {"read_enabled": bool(idx % 2)},
                    })
                    if not g_res.is_success and g_res.failure.code != "configuration_revision_conflict":
                        raise AssertionError(f"unexpected growth configure failure: {g_res.failure}")
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker_member, name="pr04-member-stress")
        t2 = threading.Thread(target=worker_state_editor, name="pr04-editor-stress")
        t1.start()
        t2.start()
        t1.join(timeout=15.0)
        t2.join(timeout=15.0)
        self.assertFalse(t1.is_alive(), "Deadlock detected in PrivateMemberSettings thread")
        self.assertFalse(t2.is_alive(), "Deadlock detected in GroupEditor thread")
        self.assertEqual(errors, [], f"Concurrency worker errors: {errors}")


class BackupMediaAndPricingCoverageTests(unittest.TestCase):
    def test_full_backup_and_restore_preserves_pcimg1_media_and_pricing_while_excluding_thumbnails(self):
        with TemporaryDirectory(prefix="pr04-backup-media-pricing-", ignore_cleanup_errors=True) as tmp:
            data_root = Path(tmp) / "data"
            runtime = StorageRuntime.open(data_root=data_root, installation_id=INSTALLATION_ID)
            try:
                # 1) Authoritative PCIMG1 conversation image stored on disk + marker in DB.
                marker = store_image(data_root, SAMPLE_PNG, mime_type="image/png")
                with runtime.transaction() as connection:
                    connection.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                        ("test.pcimg1.marker", marker.hex()),
                    )

                # 2) User-browsable generated album copy under media/generated-album/.
                album_file = data_root / "media" / "generated-album" / "role-a" / "20261120-sample.png"
                album_file.parent.mkdir(parents=True, exist_ok=True)
                album_file.write_bytes(SAMPLE_PNG)

                # 3) Regenerable thumbnail disk cache under media/album-thumbnails/ (must be excluded).
                thumb_file = data_root / "media" / "album-thumbnails" / "cached-thumb.jpg"
                thumb_file.parent.mkdir(parents=True, exist_ok=True)
                thumb_file.write_bytes(b"\xff\xd8\xff\xd9")

                # 4) Authoritative pricing settings + synced catalog under pricing/.
                pricing_store = PricingSettingsStore(data_root)
                initial = pricing_store.read()
                saved_pricing = pricing_store.update(initial["revision"], {
                    "display_currency": "USD",
                    "exchange_rate": {"mode": "manual", "manual_usd_cny": 7.35},
                })
                catalog_file = data_root / "pricing" / "catalog.json"
                catalog_file.write_text(json.dumps({"version": 1, "entries": {"gpt-4o": {"input": 2.5}}}),
                                        encoding="utf-8")

                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create(note="media-pricing-coverage").unwrap()

                self.assertIn("media", manifest["policy"]["roots"])
                self.assertIn("pricing", manifest["policy"]["roots"])
                artifact_paths = {item["path"] for item in manifest["artifacts"]}
                image_name = marker[len(b"\x00PCIMG1/"):].decode("ascii")
                self.assertIn(f"media/conversation-images/{image_name}", artifact_paths)
                self.assertIn("media/generated-album/role-a/20261120-sample.png", artifact_paths)
                self.assertIn("pricing/settings.json", artifact_paths)
                self.assertIn("pricing/catalog.json", artifact_paths)
                self.assertFalse(any(p.startswith("media/album-thumbnails/") for p in artifact_paths))
                self.assertIn(
                    {"path": "media/album-thumbnails", "reason": "transient_state"},
                    manifest["excluded"],
                )

                # Mutate live installation after backup: delete canonical image, change pricing, add post-backup junk.
                (data_root / "media" / "conversation-images" / image_name).unlink()
                post_junk = data_root / "media" / "conversation-images" / ("f" * 64 + ".png")
                post_junk.write_bytes(b"post-snapshot-junk")
                pricing_store.update(saved_pricing["revision"], {"display_currency": "CNY"})
                self.assertEqual(pricing_store.read()["display_currency"], "CNY")

                schema_version = current_schema_version(data_root)
                # Stage restore and apply on restart.
                staged = backup.restore(
                    snapshot_id=manifest["snapshot_id"],
                    confirmed_hash=manifest["content_hash"],
                )
                self.assertTrue(staged.is_success, staged.failure)
            finally:
                runtime.close()

            applied = apply_pending(
                data_root,
                installation_id=INSTALLATION_ID,
                schema_version=schema_version,
            )
            self.assertTrue(applied["applied"], applied)

            reopened = StorageRuntime.open(data_root=data_root, installation_id=INSTALLATION_ID)
            try:
                marker_hex = reopened.read_one(
                    "SELECT value FROM runtime_metadata WHERE key = 'test.pcimg1.marker'", ()
                )["value"]
                restored_marker = bytes.fromhex(marker_hex)
                # PCIMG1 image loads seamlessly with exact original bytes!
                self.assertEqual(load_image(data_root, restored_marker), SAMPLE_PNG)
                self.assertEqual((data_root / "media" / "generated-album" / "role-a" / "20261120-sample.png").read_bytes(),
                                 SAMPLE_PNG)
                # Post-snapshot junk and stale thumbnail cache are pruned cleanly.
                self.assertFalse(post_junk.exists())
                self.assertFalse(thumb_file.exists())
                # Pricing settings and catalog restored cleanly!
                restored_pricing = PricingSettingsStore(data_root).read()
                self.assertEqual(restored_pricing["display_currency"], "USD")
                self.assertAlmostEqual(restored_pricing["exchange_rate"]["manual_usd_cny"], 7.35)
                self.assertEqual(json.loads(catalog_file.read_text(encoding="utf-8"))["entries"]["gpt-4o"]["input"], 2.5)
            finally:
                reopened.close()

    def test_older_policy_snapshot_without_media_and_pricing_restores_without_pruning_them(self):
        from companion_v2.storage import backup as backup_module

        with TemporaryDirectory(prefix="pr04-backup-legacy-policy-", ignore_cleanup_errors=True) as tmp:
            data_root = Path(tmp) / "data"
            runtime = StorageRuntime.open(data_root=data_root, installation_id=INSTALLATION_ID)
            try:
                old_roots = tuple(r for r in backup_module.OWNED_ROOT_DIRECTORIES if r not in {"media", "pricing"})
                with patch.object(backup_module, "OWNED_ROOT_DIRECTORIES", old_roots):
                    backup = InstallationBackup(runtime, now=lambda: NOW)
                    manifest = backup.create(note="older-policy").unwrap()
                self.assertNotIn("media", manifest["policy"]["roots"])
                self.assertNotIn("pricing", manifest["policy"]["roots"])

                # Now add media & pricing under current policy, then restore the older snapshot.
                marker = store_image(data_root, SAMPLE_PNG, mime_type="image/png")
                pricing_store = PricingSettingsStore(data_root)
                pricing_store.update(pricing_store.read()["revision"], {"display_currency": "USD"})

                schema_version = current_schema_version(data_root)
                current_backup = InstallationBackup(runtime, now=lambda: NOW)
                staged = current_backup.restore(
                    snapshot_id=manifest["snapshot_id"],
                    confirmed_hash=manifest["content_hash"],
                )
                self.assertTrue(staged.is_success, staged.failure)
            finally:
                runtime.close()

            applied = apply_pending(
                data_root,
                installation_id=INSTALLATION_ID,
                schema_version=schema_version,
            )
            self.assertTrue(applied["applied"], applied)
            # Older snapshot's policy did not declare media/pricing, so restore must preserve them.
            self.assertEqual(load_image(data_root, marker), SAMPLE_PNG)
            self.assertEqual(PricingSettingsStore(data_root).read()["display_currency"], "USD")


class GroupImageReviewScaleAndHolTests(MessageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.review = self.messages.image_review

    @staticmethod
    def _target(part_id, batch_id):
        return {
            "part_id": part_id,
            "batch_id": batch_id,
            "image_position": "after_text",
            "images": [{
                "source": {"source_id": "generated-images-1"},
                "parent": {"source_id": "generated-images-1"},
                "descriptor": {
                    "position": 0, "mime_type": "image/png",
                    "width": 2, "height": 2, "byte_count": 8,
                    "content_hash": "0" * 64,
                },
            }],
        }

    def _seed_review(self, review_key, *, state, part_id, batch_id, created_at):
        if state == "pending":
            document = {
                **verdict_document("pending", "single", "balanced"),
                "origin_key": "origin-key",
                "role_key": "role-key",
                "group_key": "group-key",
                "created_at": stamp(created_at),
                "target": self._target(part_id, batch_id),
            }
        else:
            document = {
                **verdict_document("reviewed", "single", "balanced", label="safe",
                                   confidence=0.95, provider_ids=["primary"], revision=1),
                "origin_key": "origin-key",
                "role_key": "role-key",
                "group_key": "group-key",
                "created_at": stamp(created_at),
                "target": self._target(part_id, batch_id),
            }
            check_verdict(document)
        self.review.store(review_key, document)

    def test_row_for_part_pending_requests_and_fallback_work_beyond_256_records(self):
        # Seed 320 historical reviewed records and 320 historical fallback records + execution_tasks.
        with self.runtime.transaction() as connection:
            for idx in range(320):
                rkey = f"0000-history-{idx:04d}"
                doc = {
                    **verdict_document("reviewed", "single", "balanced", label="safe",
                                       confidence=0.95, provider_ids=["primary"], revision=1),
                    "origin_key": "origin-key", "role_key": "role-key", "group_key": "group-key",
                    "created_at": stamp(MSG_NOW + timedelta(seconds=idx)),
                    "target": self._target(f"hist-part-{idx:04d}", f"hist-batch-{idx:04d}"),
                }
                connection.execute(
                    "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                    ("conversation.image_review.v1:" + rkey, json.dumps(doc)),
                )
                connection.execute(
                    "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                    (f"conversation.image_review.fallback.v1:0000-part-{idx:04d}",
                     json.dumps({"part_id": f"0000-part-{idx:04d}", "delivery_id": f"hist-delivery-{idx:04d}"})),
                )
                connection.execute(
                    """INSERT INTO execution_tasks(
                        task_id, origin_key, kind, lane, dedupe_key, request_hash, revision,
                        state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                        created_at, updated_at, record_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        rkey, "origin-key", REVIEW_KIND, "background", rkey, rkey, 1,
                        "succeeded", 1, 1, None, stamp(MSG_NOW), stamp(MSG_NOW + timedelta(seconds=60)),
                        stamp(MSG_NOW), stamp(MSG_NOW), "{}",
                    ),
                )

        # Now add a pending review and fallback whose keys sort AFTER the first 320 entries.
        high_key = "zzzz-review-after-320"
        self._seed_review(
            high_key, state="pending", part_id="part-high-321",
            batch_id="batch-high-321", created_at=MSG_NOW + timedelta(seconds=500),
        )
        self.review.install_fallback("zzzz-part-321", "delivery-high-321", {"part_id": "zzzz-part-321", "ok": True})

        # All lookups must succeed beyond 256 records!
        found = self.review.row_for_part("part-high-321")
        self.assertIsNotNone(found)
        self.assertEqual((found["review_key"], found["state"]), (high_key, "pending"))
        self.assertTrue(self.review.pending_for({"part_id": "part-high-321"}))
        self.assertEqual([item["review_key"] for item in self.review.pending_requests(limit=2)], [high_key])

        fb = self.review.fallback_document(None, delivery_id="delivery-high-321")
        self.assertIsNotNone(fb)
        self.assertEqual(fb["part_id"], "zzzz-part-321")

        # Once a 321st execution_task claims high_key, pending_requests must NOT return it again.
        with self.runtime.transaction() as connection:
            connection.execute(
                """INSERT INTO execution_tasks(
                    task_id, origin_key, kind, lane, dedupe_key, request_hash, revision,
                    state, generation, attempt_no, attempt_id, next_run_at, deadline_at,
                    created_at, updated_at, record_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    high_key, "origin-key", REVIEW_KIND, "background", high_key, high_key, 1,
                    "running", 1, 1, None, stamp(MSG_NOW), stamp(MSG_NOW + timedelta(seconds=60)),
                    stamp(MSG_NOW), stamp(MSG_NOW), "{}",
                ),
            )
        self.assertEqual(self.review.pending_requests(limit=2), ())

    def test_advance_skips_poison_pill_and_transient_errors_without_head_of_line_starvation(self):
        # 1) Oldest pending review points to a missing batch -> permanent image_review_unavailable
        self._seed_review(
            "review-01-poison", state="pending", part_id="part-poison",
            batch_id="missing-batch", created_at=MSG_NOW,
        )
        # 2) Second oldest fails transiently once during submit
        self._seed_review(
            "review-02-transient", state="pending", part_id="part-transient",
            batch_id="batch-transient", created_at=MSG_NOW + timedelta(seconds=1),
        )
        # 3) Third pending review is healthy and must NOT be starved!
        self._seed_review(
            "review-03-healthy", state="pending", part_id="part-healthy",
            batch_id="batch-healthy", created_at=MSG_NOW + timedelta(seconds=2),
        )

        service = GroupImageReviewService(self.runtime)
        service.app = object()
        service.tasks = self.tasks
        service.attach_review(self.review)

        submitted_keys: list[str] = []
        transient_broken = True

        def fake_submit(review_key):
            if review_key == "review-01-poison":
                raise TaskError("image_review_unavailable", "the original review batch is unavailable")
            if review_key == "review-02-transient" and transient_broken:
                raise TaskError("transient_submit_gate", "temporarily busy")
            submitted_keys.append(review_key)
            return review_key

        with patch.object(service, "submit", side_effect=fake_submit):
            first_tick = service.advance()
            self.assertTrue(first_tick.is_success)
            self.assertTrue(first_tick.unwrap())

        # Healthy review was submitted in the very same tick despite 2 broken items ahead of it!
        self.assertEqual(submitted_keys, ["review-03-healthy"])
        # Permanent poison pill was finalized fail-closed to 'unavailable' in SQLite so it never blocks again.
        self.assertEqual(self.review.row("review-01-poison")["state"], "unavailable")
        self.assertEqual(service.results["review-01-poison"], {"state": "blocked", "code": "image_review_unavailable"})
        # Transient review remained pending so it can recover when the transient condition clears.
        self.assertEqual(self.review.row("review-02-transient")["state"], "pending")
        self.assertEqual(service.results["review-02-transient"], {"state": "blocked", "code": "transient_submit_gate"})

        # Next tick after transient error clears: review-02-transient is picked up cleanly!
        transient_broken = False
        with patch.object(service, "submit", side_effect=fake_submit):
            second_tick = service.advance()
            self.assertTrue(second_tick.is_success)
            self.assertTrue(second_tick.unwrap())
        self.assertEqual(submitted_keys, ["review-03-healthy", "review-02-transient"])


if __name__ == "__main__":
    unittest.main()
