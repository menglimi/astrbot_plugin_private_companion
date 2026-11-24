"""Regression tests for wardrobe pagination, bookshelf context, authoring and media capture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.native_extensions.scheduled_authoring import ScheduledAuthoring
from companion_v2.conversation.content.authoring import decode_json_object, parse_output
from companion_v2.execution.models import digest, stamp
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, VersionRef
from companion_v2.references.models import ReferenceState, WardrobeItem
from companion_v2.references.repositories import ReferenceAssetRepository
from companion_v2.role_work.life.calendar_work import routine_for
from companion_v2.role_work.models import (
    RoleActivity,
    RoleActivityState as State,
    RoleReadingReceipt,
    RoleSleepReceipt,
)
from companion_v2.role_work.work_models import RoleWorkSpec
from companion_v2.sources.models import SourceRecord
from companion_v2.storage.runtime import StorageRuntime
from tests.test_role_life_context import (
    INSTALLATION,
    NOW,
    SCOPE,
    LifeContextFixture,
)


class WardrobeActiveWithdrawnSplitTests(unittest.TestCase):
    def test_twenty_six_withdrawn_items_do_not_truncate_active_wardrobe_items(self):
        with TemporaryDirectory(prefix="companion-wardrobe-split-") as tmp:
            runtime = StorageRuntime.open(data_root=Path(tmp), installation_id="install-wardrobe")
            try:
                repo = ReferenceAssetRepository(runtime)
                owner = "role:install-wardrobe:bot-a:role-a"
                base = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)

                for idx in range(3):
                    t = base + timedelta(minutes=idx)
                    repo.put_item(
                        owner,
                        WardrobeItem(
                            item_id=f"active-{idx}",
                            name=f"在穿衣物 {idx}",
                            description="日常穿搭",
                            tags=("日常",),
                            asset_id=None,
                            state=ReferenceState.ACTIVE,
                            revision=1,
                            created_at=t,
                            updated_at=t,
                        ),
                        created_at=t,
                    )

                for idx in range(26):
                    t = base + timedelta(hours=1, minutes=idx)
                    repo.put_item(
                        owner,
                        WardrobeItem(
                            item_id=f"withdrawn-{idx}",
                            name=f"旧衣物 {idx}",
                            description="已撤回",
                            tags=("旧",),
                            asset_id=None,
                            state=ReferenceState.ACTIVE,
                            revision=1,
                            created_at=t,
                            updated_at=t,
                        ),
                        created_at=t,
                        ignore_limit=True,
                    )
                    repo.withdraw_item(
                        owner,
                        f"withdrawn-{idx}",
                        updated_at=t + timedelta(minutes=1),
                    )

                active_items = repo.list_items(owner, state=ReferenceState.ACTIVE)
                withdrawn_items = repo.list_items(owner, state=ReferenceState.WITHDRAWN, order_by_updated=True)
                self.assertEqual(len(active_items), 3)
                self.assertEqual([item.item_id for item in active_items], ["active-0", "active-1", "active-2"])
                self.assertEqual(repo.count_items(owner, state=ReferenceState.ACTIVE), 3)
                self.assertEqual(repo.count_items(owner, state=ReferenceState.WITHDRAWN), 26)
                self.assertEqual(len(withdrawn_items), 24)
                self.assertEqual(withdrawn_items[0].item_id, "withdrawn-25")
            finally:
                runtime.close()


class AuthoringScheduleAndParserTests(unittest.TestCase):
    def test_writing_calendar_regex_recognizes_common_chinese_verbs(self):
        for title in ("午后写作", "今晚写文", "深夜写稿", "安静码字", "文学创作"):
            with self.subTest(title=title):
                self.assertEqual(
                    routine_for({"kind": "role_plan", "state": "accepted", "title": title, "detail": ""}),
                    "writing",
                )

    def test_decode_json_object_tolerates_markdown_fences_and_surrounding_prose(self):
        fenced = '好的，这是草稿：\n```json\n{"title": "雨夜钢琴", "work_type": "短篇小说", "body": "雨滴敲在窗台。"}\n```\n希望喜欢。'
        parsed = decode_json_object(fenced)
        self.assertEqual(parsed["title"], "雨夜钢琴")
        draft = parse_output(fenced, action="draft")
        self.assertEqual(draft.title, "雨夜钢琴")
        self.assertEqual(draft.body, "雨滴敲在窗台。")

    def test_scheduled_authoring_retry_cooldown_and_attempt_cap(self):
        app = NS(runtime=NS(), calendar=NS(records=NS(role_key=lambda ctx: "role-key")))
        service = ScheduledAuthoring(NS(application=app))
        t0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)

        retryable = {"state": "failed", "code": "content_authoring_output", "attempts": 1, "at": stamp(t0)}
        self.assertFalse(service._can_retry_metadata(retryable, t0 + timedelta(seconds=60)))
        self.assertTrue(service._can_retry_metadata(retryable, t0 + timedelta(seconds=301)))
        exhausted = {**retryable, "attempts": ScheduledAuthoring.MAX_SLOT_ATTEMPTS}
        self.assertFalse(service._can_retry_metadata(exhausted, t0 + timedelta(hours=2)))
        permanent = {**retryable, "code": "authorization_denied"}
        self.assertFalse(service._can_retry_metadata(permanent, t0 + timedelta(hours=2)))

    def test_plan_continuation_schedules_first_work_when_library_is_empty(self):
        reserved = []
        app = NS(
            runtime=NS(),
            calendar=NS(records=NS(role_key=lambda ctx: "role-key")),
            now=lambda: NOW,
        )
        consumer = NS(application=app, access=object())
        service = ScheduledAuthoring(consumer)
        service.ready = lambda ctx, window=None: True
        admitted_actions = []

        def fake_admit(ctx, action):
            admitted_actions.append(action)
            return NS(service=NS(repository=NS(path=Path("unused.db"))))

        service.admit = fake_admit
        settings = {"planning_enabled": True, "planning_timezone": "Asia/Shanghai"}
        page = {"scan_truncated": False, "occurrences_truncated": False, "items": ()}
        with patch("astrbot_host.native_extensions.scheduled_authoring.catalogue", return_value=[]), \
                patch(
                    "astrbot_host.native_extensions.scheduled_authoring.reserve_routine_slot",
                    side_effect=lambda *args, **kwargs: reserved.append(kwargs) or "slot-1",
                ):
            result = service.plan_continuation(NS(key="ctx-1"), NOW, settings, page, sleep_plan=None)

        self.assertEqual(result, "slot-1")
        self.assertIn("create", admitted_actions)
        self.assertEqual(len(reserved), 1)
        self.assertEqual(reserved[0]["routine"], "writing")
        self.assertEqual(reserved[0]["title"], "文学创作")


class BookshelfContextAndPrivacyTests(LifeContextFixture, unittest.TestCase):
    def _record_book_chunks(self, title: str, total_chars: int, chunks: int, *, completed: bool):
        source_id = "book-" + digest([title])
        source = SourceRef(
            source_id,
            "role.reading",
            SCOPE,
            VersionRef(source_id, 1),
            NOW - timedelta(hours=3),
            NOW + timedelta(days=7),
        )
        content = "星" * total_chars
        content_hash = digest(content)
        self.messages.sources.publish(
            SourceRecord(source, self.context.audience, content_hash, content),
            observed_at=NOW - timedelta(hours=3),
        ).unwrap()
        chunk_size = total_chars // chunks
        for idx in range(chunks):
            start = idx * chunk_size
            end = total_chars if idx == chunks - 1 else (idx + 1) * chunk_size
            state = State.COMPLETED if (completed or idx < chunks - 1) else State.ACTIVE
            activity_time = NOW - timedelta(minutes=(chunks - idx) * 10)
            receipt = (
                RoleReadingReceipt(source, content_hash, total_chars, start, end, activity_time, f"读到《{title}》第 {idx + 1} 段")
                if state is State.COMPLETED else None
            )
            self.life.add_activity(
                RoleActivity(
                    activity_id=f"{source_id}-chunk-{idx}",
                    role_id=self.context.role_binding.role.subject_id,
                    scope=SCOPE,
                    started_at=activity_time - timedelta(minutes=5),
                    updated_at=activity_time,
                    state=state,
                    description=f"阅读《{title}》",
                    checkpoint=f"读到 {end} 字",
                    reading=receipt,
                    work=RoleWorkSpec(
                        kind="reading",
                        context_key=self.context.key,
                        request_id=f"req-{source_id}-{idx}",
                        source=source,
                        content_hash=content_hash,
                        start_offset=start,
                        end_offset=end,
                    ),
                )
            )
        return source

    def test_reading_book_progress_rolls_up_completion_and_never_starves_sleep(self):
        for idx in range(5):
            self._record_book_chunks(f"书目{idx}", 200, 1, completed=True)
        self._record_book_chunks("银河铁道之夜", 400, 2, completed=True)

        for sleep_idx in range(2):
            sleep_time = NOW - timedelta(hours=5 + sleep_idx * 12)
            self.life.add_activity(
                RoleActivity(
                    activity_id=f"sleep-recent-{sleep_idx}",
                    role_id=self.context.role_binding.role.subject_id,
                    scope=SCOPE,
                    started_at=sleep_time - timedelta(hours=7),
                    updated_at=sleep_time,
                    state=State.COMPLETED,
                    description=f"夜间休息 {sleep_idx}",
                    checkpoint="睡醒了",
                    sleep=RoleSleepReceipt(kind="night", base_need_minutes=480, target_minutes=480, slept_seconds=7 * 3600),
                    work=RoleWorkSpec(kind="sleep", context_key=self.context.key, request_id=f"sleep-req-{sleep_idx}", due_at=sleep_time),
                )
            )

        candidates = self.reader.candidates(self.context, NOW)
        top_six = [json.loads(options[0].payload) for options in candidates.variants[:6]]
        # Sleep must NOT be starved by 6 reading activities within the 6-activity budget (4 reading + 2 sleep).
        sleep_top = [p for p in top_six if "夜间休息" in p.get("description", "")]
        reading_top = [p for p in top_six if "reading" in p]
        self.assertEqual(len(sleep_top), 2)
        self.assertEqual(len(reading_top), 4)
        # Completed multi-chunk book carries rolled-up book_completed=True and chunk progress.
        galaxy = next(p for p in top_six if "银河铁道之夜" in p.get("description", ""))
        self.assertTrue(galaxy["reading"]["book_completed"])
        self.assertEqual(galaxy["book_progress"], {"completed": 2, "total": 2, "completed_book": True})

    def _enable_reading_share_in_group(self, enabled: bool):
        from companion_v2.role_work.life.configuration import (
            GROUP,
            LifeConfigurationOwner,
            configuration_fields,
            target_for,
        )

        owner = LifeConfigurationOwner(self.runtime)
        target = target_for(self.context)
        fields = configuration_fields()
        snapshot = owner.read(target, fields)
        owner.write(
            target,
            fields,
            changes={GROUP + ".reading_share_in_group": enabled},
            reset=(),
            expected_revision=snapshot.revision,
            observed_at=NOW,
        )

    def test_public_reading_summaries_default_off_and_opt_in_privacy_and_governance(self):
        source = self._record_book_chunks("挪威的森林", 300, 1, completed=True)
        self.diary(body="私密日记绝不能泄漏到群聊")

        group_scope = ScopeRef(
            INSTALLATION,
            SCOPE.platform,
            ScopeKind.GROUP,
            "group-999",
            SCOPE.bot_id,
            SCOPE.account_id,
            "group-999",
            "group-999",
        )
        group_ctx = NS(scope=group_scope, role_binding=self.context.role_binding, key="group-999")
        other_private_scope = ScopeRef(
            INSTALLATION,
            SCOPE.platform,
            ScopeKind.PRIVATE,
            "user-other",
            SCOPE.bot_id,
            SCOPE.account_id,
            "user-other",
            "user-other",
        )
        other_private_ctx = NS(
            scope=other_private_scope,
            role_binding=self.context.role_binding,
            key="user-other",
        )

        # Opt-in by default: disabled until explicitly enabled, preventing silent cross-window leakage.
        self.assertEqual(self.reader.public_reading_summaries(group_ctx, NOW), ())
        self.assertEqual(self.reader.public_reading_summaries(other_private_ctx, NOW), ())

        # Once explicitly enabled, both group chats and other private windows of the same role
        # receive ONLY title, state, and progress — never source text, reflections, diary, or participants.
        self._enable_reading_share_in_group(True)
        for viewer_name, viewer_ctx in (("group", group_ctx), ("other_private", other_private_ctx)):
            with self.subTest(viewer=viewer_name):
                items = self.reader.public_reading_summaries(viewer_ctx, NOW)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].purpose, "role_public_reading")
                payload = json.loads(items[0].payload)
                self.assertEqual(payload["basis"], "role_public_reading_summary")
                self.assertIn("挪威的森林", payload["title"])
                self.assertTrue(payload["completed_book"])
                self.assertEqual(payload["book_progress"], {"completed": 1, "total": 1, "completed_book": True})
                self.assertNotIn("reflection_excerpt", payload)
                self.assertNotIn("reflection", payload)
                self.assertNotIn("第 1 段", items[0].payload)
                self.assertNotIn("星星星", items[0].payload)
                self.assertNotIn("私密日记", items[0].payload)
                self.assertNotIn(self.important.subject.subject_id, items[0].payload)

        # Expired book sources are excluded via full source governance validation.
        self.assertEqual(self.reader.public_reading_summaries(group_ctx, NOW + timedelta(days=8)), ())

        # Revoking the underlying book source immediately hides it from public reading summaries.
        self.messages.sources.revoke(source, expected_governance_revision=1, observed_at=NOW).unwrap()
        self.assertEqual(self.reader.public_reading_summaries(group_ctx, NOW), ())


if __name__ == "__main__":
    unittest.main()
