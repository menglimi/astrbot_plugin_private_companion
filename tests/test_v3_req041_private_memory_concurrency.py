"""V3 REQ-041 权威私聊记忆并发回归：CAS 窗口收敛、字段级 delta、有界操作日志。

覆盖 `解决方案输出.md` 第七章问题 4 的治根改动：
- d-1：CAS 窗口收敛到单次持锁内（提交侧在同一把 `_data_lock` 内重读 revision）。
- d-2：字段级 delta 提交（写入者只声明自己的字段，不再整块覆盖他人字段）。
- 方案 A：单槽幂等 -> 按 operation_id 索引的有界操作日志（只存哈希）。
外加方案 B（per-person 串行化）与方案 C（失败写回权威记录）的并发断言。
"""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from authoritative_private_memory import (
    AuthoritativePrivateMemoryStore,
    PRIVATE_MEMORY_FIELDS,
    _digest,
    private_memory_content,
)
from astrbot_plugin_private_companion import user_memory as user_memory_module
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin
from tests.module_source_index import user_memory_mixin_tree, user_memory_source_text, file_family_source_text

ROOT = Path(__file__).resolve().parents[1]

# 5 个权威写入者的提交点（文件, 提交调用所在方法, 提交前是否在同一临界区内读取权威 revision）。
WRITER_SITES = (
    ("message_pipeline.py", "handle_private_message"),
    ("user_memory.py", "_refresh_dialogue_episode_batch"),
    ("user_memory.py", "_refresh_companion_memory_batch"),
    ("main.py", "companion_command"),
    ("page_api_users_groups.py", "update_user"),
)


class _ConcurrentMemoryHost(UserMemoryMixin):
    """最小宿主：只补 host 层能力，其余走真实 UserMemoryMixin 实现。"""

    def __init__(self) -> None:
        self.data: dict = {"users": {}}
        self._data_lock = asyncio.Lock()
        self.req041_scoped_projection_sync = object()
        self.req041_migration_status = {"scoped_required": True}
        self.enable_dialogue_episode_memory = True
        self.enable_companion_memory = True
        self.enable_open_loop_tracking = True
        self.enable_expression_learning = False
        self.max_dialogue_episodes = 12
        self.episode_memory_refresh_messages = 8
        self.episode_memory_refresh_minutes = 90
        self.memory_refresh_interval_minutes = 360
        self.max_companion_memory_items = 36
        self.raw_text = ("用户: 今天有点累, 想早点休息。\n" * 8).strip()
        self.llm_payloads: dict[str, dict] = {}
        self.llm_gate = asyncio.Event()
        self.llm_gate.set()
        self.llm_entered = 0
        self.active_llm_calls = 0
        self.max_concurrent_llm_calls = 0

    # ---- host 层最小实现 ----
    def _req041_scoped_context_for_user(self, _user, *, kind, purpose):
        return object()

    def _req041_scoped_context_ready(self, _user):
        return True

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return str(user_id or "").strip()

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        return None

    @staticmethod
    def _task_provider(*_args) -> str:
        return ""

    @staticmethod
    def _get_default_persona_prompt() -> str:
        return "默认人格"

    @staticmethod
    def _relationship_profile(_user) -> dict:
        return {"level": "熟人", "preference": "温和", "note": ""}

    @staticmethod
    def _extract_json_payload(raw: str) -> dict:
        return json.loads(raw)

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"].setdefault(user_id, {"user_id": user_id})

    async def _collect_recent_private_conversation_text(self, _user, **_kwargs) -> str:
        return self.raw_text

    async def _llm_call(self, *_args, **kwargs):
        self.llm_entered += 1
        self.active_llm_calls += 1
        self.max_concurrent_llm_calls = max(self.max_concurrent_llm_calls, self.active_llm_calls)
        try:
            # 强制让出事件循环，暴露真实的并发交错。
            await asyncio.sleep(0)
            await self.llm_gate.wait()
        finally:
            self.active_llm_calls -= 1
        return json.dumps(self.llm_payloads.get(kwargs.get("task"), {}), ensure_ascii=False)

    # ---- 测试辅助 ----
    def seed_person(self, user_id: str = "u1", person_id: str = "person-1") -> dict:
        user = {
            "user_id": user_id,
            "identity_subject_id": user_id,
            "unified_person_id": person_id,
            "episode_message_count": 12,
            "companion_memory": {
                "items": [{"text": f"事实{index}"} for index in range(3)],
                "updated_at": "2026-09-17 10:00",
            },
        }
        self.data["users"][user_id] = user
        return user

    def record(self, person_id: str = "person-1") -> dict | None:
        return AuthoritativePrivateMemoryStore(self.data).read(person_id)["record"]

    def content(self, person_id: str = "person-1") -> dict:
        record = self.record(person_id)
        return record["content"] if isinstance(record, dict) else {}

    def bootstrap_record(
        self, user_id: str = "u1", person_id: str = "person-1", *, extra: dict | None = None,
    ) -> int:
        """按 runbook 顺序先建权威记录，避免 prepare 用空白 seed 清掉用户既有字段。"""
        content = private_memory_content(self._get_user(user_id))
        content.update(extra or {})
        result = AuthoritativePrivateMemoryStore(self.data).commit(
            person_id,
            content,
            expected_revision=0,
            operation_id=f"test-bootstrap:{person_id}",
        )
        if result.get("ok") is not True:
            raise AssertionError(result)
        return int(result["revision"])

    async def wait_for_llm_entries(self, count: int) -> None:
        for _ in range(200):
            if self.llm_entered >= count:
                return
            await asyncio.sleep(0)
        raise AssertionError(f"等待 {count} 次 LLM 调用超时（实际 {self.llm_entered}）")


async def _foreground_write(host: _ConcurrentMemoryHost, user_id: str, *, label: str) -> bool:
    """模仿私聊消息主链：prepare 与 commit 在同一把 _data_lock 内（窗口内无 await）。"""
    async with host._data_lock:
        current = host._get_user(user_id)
        revision = host._req041_prepare_authoritative_private_memory(current)
        if revision is None:
            return False
        memory = current.setdefault("companion_memory", {})
        items = memory.setdefault("items", [])
        items.insert(0, {"text": label})
        committed = host._req041_commit_authoritative_private_memory(
            current,
            expected_revision=revision,
            operation_id=f"req041-private-message:{label}",
        )
        host._save_data_sync(sections={"users", "_req041_private_memory"})
        return committed


class Req041ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_dialogue_and_companion_refresh_keep_both_write_sets(self):
        """两条刷新路径 asyncio.gather 并发：互不顶掉对方字段。"""
        host = _ConcurrentMemoryHost()
        host.seed_person()
        host.bootstrap_record()
        host.llm_payloads = {
            "dialogue_episode": {"summary": "一起收拾了房间", "open_loops": ["周末去买收纳盒"]},
            "memory_profile": {"strong_memories": ["喜欢橘子汽水"]},
        }

        await asyncio.gather(
            host._maybe_refresh_dialogue_episode("u1", host._get_user("u1")),
            host._maybe_refresh_companion_memory("u1", host._get_user("u1")),
        )

        content = host.content()
        self.assertEqual("一起收拾了房间", content["dialogue_episodes"][-1]["summary"])
        self.assertEqual(["周末去买收纳盒"], [item["text"] for item in content["open_loops"]])
        self.assertEqual(
            ["喜欢橘子汽水"],
            content["companion_memory"]["profile"]["strong_memories"],
        )
        self.assertEqual(0, content["episode_message_count"])
        self.assertGreaterEqual(host.record()["revision"], 1)

    async def test_foreground_write_during_llm_window_does_not_reject_refresh(self):
        """窗口内有前台整块写入也不得产生 conflict：刷新提交侧的 base 必须新鲜。"""
        host = _ConcurrentMemoryHost()
        host.seed_person()
        host.bootstrap_record()
        host.llm_payloads = {"memory_profile": {"strong_memories": ["喜欢橘子汽水"]}}
        host.llm_gate.clear()

        refresh = asyncio.create_task(
            host._maybe_refresh_companion_memory("u1", host._get_user("u1"))
        )
        await host.wait_for_llm_entries(1)
        # LLM 在飞行中：此时前台消息链完成一次 prepare -> 写入 -> commit。
        self.assertTrue(await _foreground_write(host, "u1", label="前台事实"))
        host.llm_gate.set()
        await refresh

        content = host.content()
        self.assertEqual("前台事实", content["companion_memory"]["items"][0]["text"])
        self.assertEqual(
            ["喜欢橘子汽水"],
            content["companion_memory"]["profile"]["strong_memories"],
        )

    async def test_refresh_flow_does_not_clobber_fields_owned_by_other_writers(self):
        """delta 提交：刷新路径只写自己的字段，其他写入者的字段保持原样。"""
        host = _ConcurrentMemoryHost()
        host.seed_person()
        host.llm_payloads = {"dialogue_episode": {"summary": "一起看了电影", "open_loops": []}}
        host.bootstrap_record(extra={"intent_profile": {"mood": "calm"}, "recent_reply_topics": ["电影"]})

        await host._maybe_refresh_dialogue_episode("u1", host._get_user("u1"))

        content = host.content()
        self.assertEqual("一起看了电影", content["dialogue_episodes"][-1]["summary"])
        self.assertEqual({"mood": "calm"}, content["intent_profile"])
        self.assertEqual(["电影"], content["recent_reply_topics"])

    async def test_person_write_lock_serializes_same_person_and_keeps_cross_person_parallel(self):
        """方案 B：同 person 的两条刷新流程串行；不同 person 仍可并行。"""
        host = _ConcurrentMemoryHost()
        host.seed_person("u1", "person-1")
        host.seed_person("u2", "person-2")
        host.bootstrap_record("u1", "person-1")
        host.bootstrap_record("u2", "person-2")
        host.llm_payloads = {"dialogue_episode": {"summary": "并行检查"}}

        # 同一 person：dialogue 先持锁进入 LLM，companion 必须被挡在锁外。
        host.llm_gate.clear()
        dialogue = asyncio.create_task(
            host._maybe_refresh_dialogue_episode("u1", host._get_user("u1"))
        )
        await host.wait_for_llm_entries(1)
        companion = asyncio.create_task(
            host._maybe_refresh_companion_memory("u1", host._get_user("u1"))
        )
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertEqual(1, host.llm_entered)
        self.assertFalse(companion.done())
        host.llm_gate.set()
        await asyncio.gather(dialogue, companion)
        self.assertEqual(1, host.max_concurrent_llm_calls)

        # 不同 person：两条 dialogue 可以同时停在 LLM 内。
        host.llm_entered = 0
        host.max_concurrent_llm_calls = 0
        host.llm_gate.clear()
        host.seed_person("u3", "person-3")
        host.bootstrap_record("u3", "person-3")
        host.seed_person("u4", "person-4")
        host.bootstrap_record("u4", "person-4")
        first = asyncio.create_task(
            host._maybe_refresh_dialogue_episode("u3", host._get_user("u3"))
        )
        second = asyncio.create_task(
            host._maybe_refresh_dialogue_episode("u4", host._get_user("u4"))
        )
        await host.wait_for_llm_entries(2)
        self.assertEqual(2, host.max_concurrent_llm_calls)
        host.llm_gate.set()
        await asyncio.gather(first, second)

    async def test_refresh_failure_is_recorded_in_authoritative_record(self):
        """方案 C：写入被拒时不得静默丢弃，错误与退避必须落到权威记录里。"""
        host = _ConcurrentMemoryHost()
        host.seed_person()
        host.bootstrap_record(extra={"dialogue_episodes": [{"summary": "canonical"}]})
        host.llm_payloads = {"dialogue_episode": {"summary": "会被拒绝"}}
        original_commit = host._req041_commit_authoritative_private_memory
        calls = {"count": 0}

        def rejecting_first_commit(user, *, expected_revision, operation_id, fields=None):
            calls["count"] += 1
            if calls["count"] == 1:
                # 首次提交使用陈旧 revision，必然被字段级 CAS 拒绝。
                return original_commit(
                    user,
                    expected_revision=0,
                    operation_id=operation_id,
                    fields=fields,
                )
            return original_commit(
                user,
                expected_revision=expected_revision,
                operation_id=operation_id,
                fields=fields,
            )

        host._req041_commit_authoritative_private_memory = rejecting_first_commit
        await host._maybe_refresh_dialogue_episode("u1", host._get_user("u1"))

        self.assertGreaterEqual(calls["count"], 2)
        content = host.content()
        self.assertEqual("canonical", content["dialogue_episodes"][-1]["summary"])
        self.assertEqual(
            "private_memory_write_rejected",
            content.get("dialogue_episode_last_error"),
        )
        self.assertGreater(
            content.get("dialogue_episode_retry_after", 0),
            content.get("last_episode_refresh_at", 0),
        )

    async def test_companion_refresh_failure_is_recorded_in_authoritative_record(self):
        """陪伴画像提交被拒时也必须写入错误与退避，不能静默返回。"""
        host = _ConcurrentMemoryHost()
        host.seed_person()
        host.bootstrap_record()
        host.llm_payloads = {
            "memory_profile": {"strong_memories": ["会被拒绝"]},
        }
        original_commit = host._req041_commit_authoritative_private_memory
        calls = {"count": 0}

        def rejecting_first_commit(user, *, expected_revision, operation_id, fields=None):
            calls["count"] += 1
            if calls["count"] == 1:
                return original_commit(
                    user,
                    expected_revision=0,
                    operation_id=operation_id,
                    fields=fields,
                )
            return original_commit(
                user,
                expected_revision=expected_revision,
                operation_id=operation_id,
                fields=fields,
            )

        host._req041_commit_authoritative_private_memory = rejecting_first_commit
        await host._maybe_refresh_companion_memory("u1", host._get_user("u1"))

        self.assertGreaterEqual(calls["count"], 2)
        content = host.content()
        self.assertNotIn("profile", content["companion_memory"])
        self.assertEqual(
            "private_memory_write_rejected",
            content.get("companion_memory_last_error"),
        )
        self.assertGreater(
            content.get("companion_memory_retry_after", 0),
            content.get("last_memory_refresh_at", 0),
        )


class Req041StoreContractTests(unittest.TestCase):
    def test_legacy_whole_record_stale_commit_still_loses_the_other_batch(self):
        """对照实验：HEAD 形态（整块替换 + LLM 前读到的 revision）必然 conflict 并丢弃本批产物。

        这是生产日志 `code=private_memory_revision_conflict` 的最小复现：
        两条刷新路径都在 base 处读到 revision，后提交者的整块写入被 CAS 拒绝。
        """
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        base = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "旧事实"}]}},
            expected_revision=0,
            operation_id="bootstrap",
        )["revision"]
        dialogue = store.commit(
            "person-1",
            {
                "companion_memory": {"items": [{"text": "旧事实"}]},
                "dialogue_episodes": [{"summary": "s"}],
            },
            expected_revision=base,
            operation_id="dialogue-batch",
        )
        self.assertEqual("updated", dialogue["code"])
        companion = store.commit(
            "person-1",
            {
                "companion_memory": {"items": [{"text": "旧事实"}, {"text": "新事实"}]},
                "last_memory_refresh_at": 1.0,
            },
            expected_revision=base,
            operation_id="memory-batch",
        )
        self.assertFalse(companion["ok"])
        self.assertEqual("private_memory_revision_conflict", companion["code"])
        self.assertNotIn("last_memory_refresh_at", store.read("person-1")["record"]["content"])

    def test_declared_write_sets_turn_the_same_interleaving_into_a_merge(self):
        """同一交错在字段级 delta 下变成合并：两批产物都留下。"""
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        global_base = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "旧事实"}]}},
            expected_revision=0,
            operation_id="bootstrap",
        )["revision"]
        dialogue = store.commit(
            "person-1",
            {
                "dialogue_episodes": [{"summary": "s"}],
                "episode_message_count": 0,
                "last_episode_refresh_at": 1.0,
            },
            expected_revision=global_base,
            operation_id="dialogue-batch",
            fields=("dialogue_episodes", "episode_message_count", "last_episode_refresh_at"),
        )
        self.assertEqual("updated", dialogue["code"])
        companion = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "旧事实"}, {"text": "新事实"}]}},
            expected_revision=global_base,
            operation_id="memory-batch",
            fields=("companion_memory",),
        )
        self.assertEqual("updated", companion["code"])
        content = store.read("person-1")["record"]["content"]
        self.assertEqual([{"summary": "s"}], content["dialogue_episodes"])
        self.assertEqual(1.0, content["last_episode_refresh_at"])
        self.assertEqual(
            [{"text": "旧事实"}, {"text": "新事实"}],
            content["companion_memory"]["items"],
        )

    def test_same_field_stale_write_is_still_rejected(self):
        """字段级 CAS 不放行同字段陈旧写入：真正的 lost-update 仍然 fail closed。"""
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        first = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "canonical"}]}},
            expected_revision=0,
            operation_id="first",
            fields=("companion_memory",),
        )
        self.assertEqual("created", first["code"])
        advanced = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "newer"}]}},
            expected_revision=1,
            operation_id="second",
            fields=("companion_memory",),
        )
        self.assertEqual("updated", advanced["code"])

        stale = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "stale-overwrite"}]}},
            expected_revision=1,
            operation_id="third",
            fields=("companion_memory",),
        )
        self.assertFalse(stale["ok"])
        self.assertEqual("private_memory_revision_conflict", stale["code"])
        self.assertEqual(
            "newer",
            store.read("person-1")["record"]["content"]["companion_memory"]["items"][0]["text"],
        )

    def test_stale_write_after_field_deletion_is_still_rejected(self):
        """删除也必须留下 revision tombstone，防止陈旧任务把字段复活。"""
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        created = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "canonical"}]}},
            expected_revision=0,
            operation_id="create",
            fields=("companion_memory",),
        )
        self.assertEqual("created", created["code"])
        deleted = store.commit(
            "person-1",
            {},
            expected_revision=1,
            operation_id="delete",
            fields=("companion_memory",),
        )
        self.assertEqual("updated", deleted["code"])
        self.assertNotIn(
            "companion_memory",
            store.read("person-1")["record"]["content"],
        )

        stale = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "stale-revival"}]}},
            expected_revision=1,
            operation_id="stale-after-delete",
            fields=("companion_memory",),
        )

        self.assertFalse(stale["ok"])
        self.assertEqual("private_memory_revision_conflict", stale["code"])
        self.assertNotIn(
            "companion_memory",
            store.read("person-1")["record"]["content"],
        )

    def test_legacy_whole_record_backfill_protects_absent_fields(self):
        """旧版整块记录中的缺失字段也代表在该 revision 被删除。"""
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        created = store.commit(
            "person-1",
            {"dialogue_episodes": [{"summary": "legacy"}]},
            expected_revision=0,
            operation_id="legacy-create",
        )
        self.assertEqual("created", created["code"])
        record = snapshot["_req041_private_memory"]["records"]["person-1"]
        record.pop("field_revisions", None)
        record.pop("operations", None)

        stale = store.commit(
            "person-1",
            {"companion_memory": {"items": [{"text": "stale-revival"}]}},
            expected_revision=0,
            operation_id="stale-after-upgrade",
            fields=("companion_memory",),
        )

        self.assertFalse(stale["ok"])
        self.assertEqual("private_memory_revision_conflict", stale["code"])
        self.assertNotIn(
            "companion_memory",
            store.read("person-1")["record"]["content"],
        )

    def test_disjoint_write_sets_accept_a_stale_base_without_losing_fields(self):
        """disjoint 写集：陈旧 base 不再产生伪冲突，双方字段都必须保留。"""
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        dialogue = store.commit(
            "person-1",
            {"dialogue_episodes": [{"summary": "s1"}]},
            expected_revision=0,
            operation_id="dialogue-batch",
            fields=("dialogue_episodes",),
        )
        self.assertEqual("created", dialogue["code"])
        companion = store.commit(
            "person-1",
            {"companion_memory": {"profile": {"strong_memories": ["橘子汽水"]}}},
            expected_revision=0,
            operation_id="memory-batch",
            fields=("companion_memory",),
        )
        self.assertEqual("updated", companion["code"])
        content = store.read("person-1")["record"]["content"]
        self.assertEqual(
            {"companion_memory", "dialogue_episodes"},
            set(content),
        )

    def test_operation_log_is_bounded_replays_after_interleaving_and_stores_hashes_only(self):
        """有界操作日志：被其他写入者穿插后仍能幂等重放，且只持久化哈希。"""
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        first = store.commit(
            "person-1",
            {"open_loops": [{"text": "one"}]},
            expected_revision=0,
            operation_id="secret-operation-value",
        )
        self.assertEqual("created", first["code"])
        for index in range(5):
            store.commit(
                "person-1",
                {"episode_message_count": index},
                expected_revision=store.read("person-1")["record"]["revision"],
                operation_id=f"interleaved-{index}",
                fields=("episode_message_count",),
            )
        replay = store.commit(
            "person-1",
            {"open_loops": [{"text": "one"}]},
            expected_revision=0,
            operation_id="secret-operation-value",
        )
        self.assertEqual("idempotent", replay["code"])

        for index in range(60):
            store.commit(
                "person-1",
                {"intent_profile": {"mood": f"m{index}"}},
                expected_revision=store.read("person-1")["record"]["revision"],
                operation_id=f"bulk-{index}",
                fields=("intent_profile",),
            )
        record = store.read("person-1")["record"]
        self.assertLessEqual(len(record["operations"]), 32)
        serialized = repr(record["operations"])
        self.assertNotIn("secret-operation-value", serialized)
        self.assertNotIn("bulk-59", serialized)

    def test_legacy_single_slot_idempotency_survives_upgrade(self):
        """升级后仍识别旧版最后一笔成功操作，不把安全重试误报为冲突。"""
        person_id = "person-1"
        operation_id = "legacy-operation"
        content = {"open_loops": [{"text": "one"}]}
        content_hash = _digest(content)
        request_hash = _digest(
            {
                "person_id": person_id,
                "expected_revision": 0,
                "content_hash": content_hash,
            }
        )
        snapshot = {
            "_req041_private_memory": {
                "schema": "req041.person_private_memory.v1",
                "records": {
                    person_id: {
                        "schema": "req041.person_private_memory.v1",
                        "revision": 1,
                        "content": content,
                        "content_hash": content_hash,
                        "updated_at": 1.0,
                        "last_operation_hash": hashlib.sha256(
                            operation_id.encode("utf-8")
                        ).hexdigest(),
                        "last_request_hash": request_hash,
                    }
                },
            }
        }
        store = AuthoritativePrivateMemoryStore(snapshot)

        replay = store.commit(
            person_id,
            content,
            expected_revision=0,
            operation_id=operation_id,
        )

        self.assertTrue(replay["ok"])
        self.assertEqual("idempotent", replay["code"])
        self.assertEqual(1, replay["revision"])

    def test_undeclared_write_set_keeps_the_whole_record_replacement_contract(self):
        """未声明写集时仍是整块替换契约（前台 3 个写入者行为不变）。"""
        user = {"companion_memory": {"items": []}, "relationship_score": 9, "open_loops": []}
        self.assertEqual({"companion_memory": {"items": []}}, private_memory_content(user))
        snapshot: dict = {}
        store = AuthoritativePrivateMemoryStore(snapshot)
        store.commit(
            "person-1",
            {"open_loops": [{"text": "keep"}]},
            expected_revision=0,
            operation_id="seed",
        )
        replaced = store.commit(
            "person-1",
            private_memory_content({"companion_memory": {"items": [{"text": "x"}]}}),
            expected_revision=1,
            operation_id="replace",
        )
        self.assertEqual("updated", replaced["code"])
        self.assertEqual({"companion_memory"}, set(store.read("person-1")["record"]["content"]))
        self.assertEqual(
            deepcopy(store.read("person-1")["record"]["content"]),
            replaced["record"]["content"],
        )

    def test_declared_write_sets_are_disjoint_and_within_memory_fields(self):
        for name in ("open_loops", "dialogue_episodes", "episode_message_count"):
            self.assertIn(name, user_memory_module._REQ041_DIALOGUE_EPISODE_FIELDS)
        self.assertIn("companion_memory", user_memory_module._REQ041_COMPANION_MEMORY_FIELDS)
        self.assertEqual(
            set(),
            set(user_memory_module._REQ041_DIALOGUE_EPISODE_FIELDS)
            & set(user_memory_module._REQ041_COMPANION_MEMORY_FIELDS),
        )
        for fields in (
            user_memory_module._REQ041_DIALOGUE_EPISODE_FIELDS,
            user_memory_module._REQ041_COMPANION_MEMORY_FIELDS,
        ):
            self.assertTrue(set(fields) <= set(PRIVATE_MEMORY_FIELDS))
            self.assertEqual(len(fields), len(set(fields)))

    def test_dialogue_operation_id_follows_llm_output_not_input_hash(self):
        """方案 E：同一段输入的不同产物必须得到不同的 operation_id。"""
        source = user_memory_source_text(ROOT)
        dialogue_source = source[
            source.index("async def _refresh_dialogue_episode_batch"):
            source.index("def _build_expression_decision_for_user")
        ]
        self.assertIn("episode_fingerprint", dialogue_source)
        self.assertNotIn(
            'operation_id=f"req041-dialogue-episode:{user_id}:{expression_batch_key}"',
            dialogue_source,
        )
        self.assertIn(
            'operation_id=f"req041-dialogue-episode:{user_id}:{episode_fingerprint}"',
            dialogue_source,
        )


class Req041CriticalSectionTests(unittest.TestCase):
    def _module_tree(self, name: str) -> ast.Module:
        if name == "user_memory.py":
            return user_memory_mixin_tree(ROOT)
        return ast.parse(file_family_source_text(ROOT, name))

    def _method(self, tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
                return node
        raise AssertionError(f"method not found: {name}")

    @staticmethod
    def _data_lock_blocks(node: ast.AST) -> list[ast.AsyncWith]:
        blocks = []
        for child in ast.walk(node):
            if isinstance(child, ast.AsyncWith) and any(
                "_data_lock" in (ast.unparse(item.context_expr) or "") for item in child.items
            ):
                blocks.append(child)
        return blocks

    @staticmethod
    def _all_data_lock_blocks(tree: ast.Module) -> list[ast.AsyncWith]:
        """收集模块中所有持有 _data_lock 的 async with 块（不限于某个方法）。"""
        result: list[ast.AsyncWith] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result.extend(
                    block
                    for block in ast.walk(node)
                    if isinstance(block, ast.AsyncWith)
                    and any(
                        "_data_lock" in (ast.unparse(item.context_expr) or "")
                        for item in block.items
                    )
                )
        return result

    def test_every_authoritative_writer_commits_inside_one_lock_held_window(self):
        """CAS 窗口收敛：提交与权威 revision 读取必须同处一段 _data_lock 临界区，且无 await 间隔。

        重构后提交点常下沉到同名辅助函数（如 _handle_private_message_lock_memory），
        而 _data_lock 临界区仍留在宿主壳（handle_private_message）。本测试须跨函数感知：
        当辅助函数自身不含 _data_lock 块时，改校验"宿主对辅助函数的调用点"是否落在
        宿主的 _data_lock 块内；prepare/await 不变量则在辅助函数（prepare 与 commit
        实际同处之处）内校验。
        """
        for filename, method_name in WRITER_SITES:
            with self.subTest(writer=f"{filename}:{method_name}"):
                tree = self._module_tree(filename)
                host = self._method(tree, method_name)

                # 先尝试在宿主方法内直接定位提交点（保留原始单函数语义）
                direct_commits = self._memory_api_sites(host, "commit")
                direct_locks = self._data_lock_blocks(host)
                # 每个扫描单元 = (承载 prepare/commit 的函数, 该函数自身锁块 或 None=锁在宿主)
                scan_units: list[tuple[ast.AST, list[ast.AsyncWith] | None]] = [
                    (host, direct_locks)
                ]
                commits: list[int] = list(direct_commits)

                if not direct_commits:
                    # 重构后提交点常下沉到同名辅助函数；锁仍留在宿主壳
                    candidate_names = [
                        n.name for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and method_name in n.name
                    ]
                    host_lock_blocks = self._data_lock_blocks(host)
                    for name in candidate_names:
                        cand = self._method(tree, name)
                        cand_commits = self._memory_api_sites(cand, "commit")
                        if not cand_commits:
                            continue
                        cand_locks = self._data_lock_blocks(cand)
                        if cand_locks:
                            # 辅助函数自带锁块：按原单函数逻辑处理
                            scan_units.append((cand, cand_locks))
                            commits.extend(cand_commits)
                            continue
                        # 辅助函数无锁块：锁在宿主，需验证"宿主对辅助函数的调用点"落在宿主锁内
                        self.assertTrue(
                            host_lock_blocks,
                            f"{filename}:{method_name} 宿主函数没有 _data_lock 块，"
                            f"却把提交下沉到无锁辅助函数 {name}",
                        )
                        call_lines = [
                            node.lineno for node in ast.walk(host)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == name
                        ]
                        self.assertTrue(
                            call_lines,
                            f"{filename}:{method_name} 未在宿主内找到对 {name} 的调用",
                        )
                        for call_line in call_lines:
                            enclosing = [
                                block for block in host_lock_blocks
                                if block.lineno <= call_line <= (block.end_lineno or block.lineno)
                            ]
                            self.assertTrue(
                                enclosing,
                                f"{filename}:{method_name} 对提交辅助函数 {name} 的调用"
                                f"(行 {call_line}) 未落在宿主 _data_lock 内",
                            )
                        # prepare/await 不变量在辅助函数内校验（prepare 与 commit 同处）
                        scan_units.append((cand, None))
                        commits.extend(cand_commits)

                self.assertTrue(commits, f"{filename}:{method_name} 没有提交点")
                # 对每个承载提交的函数单元做 prepare/await 不变量校验
                for unit_func, unit_locks in scan_units:
                    unit_commits = self._memory_api_sites(unit_func, "commit")
                    if not unit_commits:
                        continue
                    for commit_line in unit_commits:
                        if unit_locks is not None:
                            enclosing = [
                                block for block in unit_locks
                                if block.lineno <= commit_line <= (block.end_lineno or block.lineno)
                            ]
                            self.assertTrue(
                                enclosing,
                                f"{filename}:{method_name} 的提交点未持有 _data_lock",
                            )
                            block = min(enclosing, key=lambda item: item.lineno)
                        else:
                            # 锁在宿主层已校验；以整个辅助函数作为 prepare/await 扫描范围
                            block = unit_func
                        prepares = self._memory_api_sites(block, "prepare")
                        self.assertTrue(
                            prepares,
                            f"{filename}:{method_name} 未在同一临界区内重读权威 revision",
                        )
                        lower = max(prepares)
                        waits = [
                            node.lineno for node in ast.walk(block)
                            if isinstance(node, ast.Await) and lower < node.lineno < commit_line
                        ]
                        self.assertEqual(
                            [],
                            waits,
                            f"{filename}:{method_name} 在权威 revision 读取与提交之间跨了 await",
                        )

    @staticmethod
    def _memory_api_sites(node: ast.AST, action: str) -> list[int]:
        """定位 REQ-041 记忆 API 调用点：直接调用，或经 getattr 字符串间接取用。"""
        suffix = f"_req041_{action}_authoritative_private_memory"
        sites = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and ast.unparse(child.func).endswith(suffix):
                sites.append(child.lineno)
            if isinstance(child, ast.Constant) and child.value == suffix:
                sites.append(child.lineno)
        return sites

    def test_background_refresh_reads_revision_only_next_to_commit(self):
        """后台刷新路径不得在 LLM 调用前读取权威 revision（旧缺陷形态）。"""
        tree = self._module_tree("user_memory.py")
        for method_name in ("_refresh_dialogue_episode_batch", "_refresh_companion_memory_batch"):
            with self.subTest(method=method_name):
                method = self._method(tree, method_name)
                prepares = self._memory_api_sites(method, "prepare")
                llm_calls = [
                    node.lineno for node in ast.walk(method)
                    if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("_llm_call")
                ]
                self.assertTrue(prepares, method_name)
                self.assertTrue(llm_calls, method_name)
                self.assertGreater(
                    min(prepares),
                    max(llm_calls),
                    f"{method_name} 的权威 revision 仍在 LLM 调用之前读取",
                )


if __name__ == "__main__":
    unittest.main()
