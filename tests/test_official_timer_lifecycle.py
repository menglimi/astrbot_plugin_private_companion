# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest
from datetime import datetime
from types import SimpleNamespace

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _FakeCronDatabase:
    def __init__(self, manager: "_FakeCronManager") -> None:
        self.manager = manager

    async def get_cron_job(self, job_id: str):
        return self.manager.jobs.get(job_id)


class _FakeCronManager:
    def __init__(self) -> None:
        self.jobs: dict[str, SimpleNamespace] = {}
        self.db = _FakeCronDatabase(self)
        self.created = 0
        self.fail_next_add = False
        self.fail_delete_ids: set[str] = set()
        self.pause_next_add = False
        self.add_started = asyncio.Event()
        self.add_release = asyncio.Event()

    async def add_active_job(self, **kwargs):
        if self.pause_next_add:
            self.pause_next_add = False
            self.add_started.set()
            await self.add_release.wait()
        if self.fail_next_add:
            self.fail_next_add = False
            raise RuntimeError("simulated add failure")
        self.created += 1
        job_id = f"job-{self.created}"
        payload = dict(kwargs.get("payload") or {})
        run_at = kwargs.get("run_at")
        if run_at is not None:
            payload["run_at"] = run_at.isoformat()
        job = SimpleNamespace(job_id=job_id, status="scheduled", payload=payload)
        self.jobs[job_id] = job
        return job

    async def delete_job(self, job_id: str) -> None:
        if job_id in self.fail_delete_ids:
            raise RuntimeError("simulated delete failure")
        self.jobs.pop(job_id, None)


class _CronEvent:
    def __init__(self, *, timer_id: str, user_id: str, job_id: str, origin: str = "private_companion_timer") -> None:
        self.extras = {
            "cron_payload": {
                "origin": origin,
                "sender_id": user_id,
                "private_companion": {"timer_id": timer_id},
            },
            "cron_job": {"id": job_id},
        }

    def get_extra(self, key=None, default=None):
        if key is None:
            return self.extras
        return self.extras.get(key, default)


class _TimerHarness(UserMemoryMixin, DailyStateMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self._llm_timer_operation_locks: dict[str, asyncio.Lock] = {}
        self.cron = _FakeCronManager()
        self.context = SimpleNamespace(cron_manager=self.cron)
        self.environment_perception_timezone = "Asia/Shanghai"
        self.schedule_persona_prompt = ""
        self.persona_proactive_voice_prompt = ""
        self.data = {
            "users": {
                "owner": {
                    "user_id": "owner",
                    "enabled": True,
                    "umo": "default:FriendMessage:owner",
                }
            }
        }
        self.saved = 0
        self.lifecycle_tasks: list[asyncio.Task] = []

    def _get_user(self, user_id: str):
        return self.data.setdefault("users", {}).setdefault(user_id, {"user_id": user_id})

    def _save_data_sync(self) -> None:
        self.saved += 1

    def _user_enabled_for_proactive(self, _user_id, _user) -> bool:
        return True

    def _friend_can_receive_proactive_reason(self, _user, _reason, _action="") -> bool:
        return True

    def _normalize_internal_motive_text(self, text: str) -> str:
        return str(text or "")

    def _clear_pending_proactive_plan(self, user) -> None:
        user["next_proactive_at"] = 0
        user["planned_proactive_source"] = ""

    def _private_user_role(self, _user, _user_id="") -> str:
        return "owner"

    def _environment_fromtimestamp(self, value: float) -> datetime:
        return datetime.fromtimestamp(float(value))

    def _is_initial_wakeup_greeting(self, _user) -> bool:
        return False

    def _inbound_satisfies_greeting(self, reason: str, *, now=None) -> bool:
        return reason == "morning_greeting"

    def _mark_greeting_satisfied_by_inbound(self, user, reason: str) -> bool:
        user["satisfied_greeting"] = reason
        return True

    def _create_lifecycle_background_task(self, operation, *, label: str):
        task = asyncio.create_task(operation, name=label)
        self.lifecycle_tasks.append(task)
        return task

    @staticmethod
    def timer_payload(topic: str) -> dict:
        return {
            "scheduled_ts": time.time() + 3600,
            "reason": "check_in",
            "action": "message",
            "topic": topic,
            "motive": f"remember {topic}",
        }

    async def schedule(self, topic: str) -> None:
        await self._schedule_llm_timer(
            "owner",
            self.timer_payload(topic),
            source_text=topic,
            source_origin="test",
            trigger_umo="default:FriendMessage:owner",
        )


class OfficialTimerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_cron_event_records_trigger_delivery_and_completion(self) -> None:
        harness = _TimerHarness()
        await harness.schedule("first")
        timer = dict(harness.data["users"]["owner"]["llm_timer_event"])
        event = _CronEvent(timer_id=timer["id"], user_id="owner", job_id=timer["job_id"])

        self.assertTrue(await harness._acknowledge_official_llm_timer_trigger(event))
        self.assertEqual(harness.data["users"]["owner"]["llm_timer_event"]["status"], "triggered")

        tool = SimpleNamespace(name="send_message_to_user")
        result = SimpleNamespace(
            isError=False,
            content=[SimpleNamespace(text="Message sent to session default:FriendMessage:owner")],
        )
        self.assertTrue(await harness._record_official_llm_timer_tool_result(event, tool, result))
        self.assertTrue(await harness._complete_official_llm_timer_event(event))
        current = harness.data["users"]["owner"]["llm_timer_event"]
        self.assertEqual(current["status"], "completed")
        self.assertEqual(current["delivery_status"], "sent")

    async def test_wrong_ids_and_generic_official_task_leave_timer_unchanged(self) -> None:
        harness = _TimerHarness()
        await harness.schedule("first")
        timer = dict(harness.data["users"]["owner"]["llm_timer_event"])

        wrong_timer = _CronEvent(timer_id="other", user_id="owner", job_id=timer["job_id"])
        wrong_job = _CronEvent(timer_id=timer["id"], user_id="owner", job_id="other-job")
        generic = _CronEvent(
            timer_id=timer["id"],
            user_id="owner",
            job_id=timer["job_id"],
            origin="tool",
        )
        self.assertFalse(await harness._acknowledge_official_llm_timer_trigger(wrong_timer))
        self.assertFalse(await harness._acknowledge_official_llm_timer_trigger(wrong_job))
        self.assertFalse(await harness._acknowledge_official_llm_timer_trigger(generic))
        self.assertEqual(harness.data["users"]["owner"]["llm_timer_event"], timer)

    async def test_two_concurrent_schedules_leave_one_matching_official_job(self) -> None:
        harness = _TimerHarness()
        await asyncio.gather(harness.schedule("first"), harness.schedule("second"))

        current = harness.data["users"]["owner"]["llm_timer_event"]
        self.assertEqual(current["status"], "scheduled")
        self.assertEqual(current["topic"], "second")
        self.assertEqual(set(harness.cron.jobs), {current["job_id"]})

    async def test_schedule_racing_cancel_cannot_leave_an_orphan(self) -> None:
        harness = _TimerHarness()
        harness.cron.pause_next_add = True
        schedule_task = asyncio.create_task(harness.schedule("first"))
        await harness.cron.add_started.wait()
        cancel_task = asyncio.create_task(
            harness._cancel_llm_timer(
                "owner",
                {"cancel": True},
                source_text="cancel",
                source_origin="test",
            )
        )
        await asyncio.sleep(0)
        harness.cron.add_release.set()
        await asyncio.gather(schedule_task, cancel_task)

        current = harness.data["users"]["owner"]["llm_timer_event"]
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual(harness.cron.jobs, {})

    async def test_replacement_add_failure_preserves_old_state_and_job(self) -> None:
        harness = _TimerHarness()
        await harness.schedule("old")
        old = dict(harness.data["users"]["owner"]["llm_timer_event"])
        harness.cron.fail_next_add = True

        await harness.schedule("new")

        current = harness.data["users"]["owner"]["llm_timer_event"]
        self.assertEqual(current["id"], old["id"])
        self.assertEqual(current["job_id"], old["job_id"])
        self.assertIn(old["job_id"], harness.cron.jobs)
        self.assertIn("simulated add failure", current["last_replace_error"])

    async def test_old_delete_failure_rolls_back_new_job(self) -> None:
        harness = _TimerHarness()
        await harness.schedule("old")
        old = dict(harness.data["users"]["owner"]["llm_timer_event"])
        harness.cron.fail_delete_ids.add(old["job_id"])

        await harness.schedule("new")

        current = harness.data["users"]["owner"]["llm_timer_event"]
        self.assertEqual(current["id"], old["id"])
        self.assertEqual(current["job_id"], old["job_id"])
        self.assertEqual(set(harness.cron.jobs), {old["job_id"]})

    async def test_running_old_job_is_never_reused_for_replacement(self) -> None:
        harness = _TimerHarness()
        await harness.schedule("old")
        old = dict(harness.data["users"]["owner"]["llm_timer_event"])
        harness.cron.jobs[old["job_id"]].status = "running"

        await harness.schedule("new")

        current = harness.data["users"]["owner"]["llm_timer_event"]
        self.assertNotEqual(current["job_id"], old["job_id"])
        self.assertEqual(current["previous_running_job_id"], old["job_id"])
        self.assertEqual(set(harness.cron.jobs), {old["job_id"], current["job_id"]})

    async def test_inbound_greeting_cancellation_deletes_official_job_before_clearing_state(self) -> None:
        harness = _TimerHarness()
        await harness.schedule("morning")
        user = harness.data["users"]["owner"]
        user["llm_timer_event"]["reason"] = "morning_greeting"
        job_id = user["llm_timer_event"]["job_id"]

        changed = harness._cancel_inbound_conflicting_greeting(
            user,
            now=time.time(),
            user_id="owner",
            trigger_umo="default:FriendMessage:owner",
        )
        self.assertTrue(changed)
        self.assertIn(job_id, harness.cron.jobs)
        await asyncio.gather(*harness.lifecycle_tasks)

        self.assertNotIn(job_id, harness.cron.jobs)
        self.assertEqual(user["llm_timer_event"]["status"], "cancelled")

    def test_official_timer_never_becomes_internal_due_timer(self) -> None:
        harness = _TimerHarness()
        timer = {
            "id": "timer-1",
            "backend": "astrbot_cron",
            "status": "scheduled",
            "job_id": "job-1",
            "scheduled_ts": time.time() - 60,
        }
        self.assertEqual(harness._due_internal_llm_timer_id({"llm_timer_event": timer}), "")

    def test_past_timer_expires_unconfirmed_without_claiming_completion(self) -> None:
        harness = _TimerHarness()
        harness.data["users"]["owner"]["llm_timer_event"] = {
            "id": "timer-1",
            "backend": "astrbot_cron",
            "status": "scheduled",
            "job_id": "job-1",
            "scheduled_ts": time.time() - 3600,
        }

        self.assertEqual(harness._expire_stale_official_llm_timers_locked(), 1)
        status = harness.data["users"]["owner"]["llm_timer_event"]["status"]
        self.assertEqual(status, "expired_unconfirmed")
        self.assertNotEqual(status, "completed")


if __name__ == "__main__":
    unittest.main()
