"""Tests for optional structured status levels produced during state maintenance."""

import asyncio
from datetime import timedelta
import json
import unittest

from companion_v2.execution.models import TaskError, encode
from companion_v2.role_work.state.maintenance import PROMPT, StateMaintenance
from companion_v2.role_work.state.service import FIELDS, STATUS_LEVELS, validate_state
from tests.test_management_access import NOW
from tests.test_role_state import StateFixture, state_data
from tests.test_role_tool_loop import answered


class StructuredStatusLevelContractTests(unittest.TestCase):
    def test_validate_state_remains_backward_compatible_without_level_fields(self):
        legacy_full = state_data(health="没有什么酸沉或病痛不适", hunger="不饿")
        validated = validate_state(legacy_full, NOW)
        self.assertEqual(set(validated), set(FIELDS))
        self.assertEqual(validated["health"], "没有什么酸沉或病痛不适")

        legacy_without_online = {k: v for k, v in legacy_full.items() if k != "online_mode"}
        validated_no_online = validate_state(legacy_without_online, NOW)
        self.assertEqual(set(validated_no_online), set(FIELDS))
        self.assertIsNone(validated_no_online["online_mode"])

    def test_validate_state_accepts_optional_levels_and_drops_orphan_or_null_levels(self):
        payload = state_data(
            health="睡饱后浑身暖和舒展，没有什么酸沉或病痛不适",
            health_level="well",
            hunger="胃里有点空，想找点热乎东西吃",
            hunger_level="hungry",
            sleep=None,
            sleep_level="awake",
            busy="在客厅闲着，眼下没什么正事忙",
            busy_level="free",
        )
        validated = validate_state(payload, NOW)
        self.assertEqual(validated["health_level"], "well")
        self.assertEqual(validated["hunger_level"], "hungry")
        self.assertEqual(validated["busy_level"], "free")
        # sleep is None, so orphan sleep_level is dropped cleanly
        self.assertNotIn("sleep_level", validated)

    def test_validate_state_rejects_invalid_level_values_and_unknown_keys(self):
        bad_level = state_data(health="有点头疼", health_level="critical")
        with self.assertRaises(TaskError) as ctx:
            validate_state(bad_level, NOW)
        self.assertEqual(ctx.exception.code, "role_state_contract")

        bad_type = state_data(health="有点头疼", health_level=123)
        with self.assertRaises(TaskError) as ctx:
            validate_state(bad_type, NOW)
        self.assertEqual(ctx.exception.code, "role_state_contract")

        unknown_key = state_data(health="良好", unexpected_slot="x")
        with self.assertRaises(TaskError) as ctx:
            validate_state(unknown_key, NOW)
        self.assertEqual(ctx.exception.code, "role_state_contract")

    def test_maintenance_sanitizer_normalizes_synonyms_and_drops_malformed_levels(self):
        payload = state_data(
            health="没有不适",
            health_level=" Good ",
            hunger="有点饿",
            hunger_level="饿了",
            sleep="翻来覆去",
            sleep_level="insomnia",
            busy="闲着",
            busy_level="bogus_value",
        )
        StateMaintenance._sanitize_status_levels(payload)
        self.assertEqual(payload["health_level"], "well")
        self.assertEqual(payload["hunger_level"], "hungry")
        self.assertEqual(payload["sleep_level"], "restless")
        self.assertNotIn("busy_level", payload)

        # Non-string malformed values are dropped cleanly rather than failing the round
        bad_types = state_data(health="良好", health_level=["well"], hunger="不饿", hunger_level=True)
        StateMaintenance._sanitize_status_levels(bad_types)
        self.assertNotIn("health_level", bad_types)
        self.assertNotIn("hunger_level", bad_types)

    def test_maintenance_prompt_documents_optional_level_enums(self):
        for key in STATUS_LEVELS:
            self.assertIn(key, PROMPT)


class StructuredStatusLevelProjectionTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_write_and_projection_preserve_levels_and_hide_when_feature_disabled(self):
        _, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, health_enabled=True, hunger_enabled=True,
                       sleep_enabled=True, busy_enabled=True)
        payload = state_data(
            energy=86,
            health="睡饱后浑身暖和舒展，没有什么酸沉或病痛不适",
            health_level="well",
            hunger="快到午饭点了，有点饿",
            hunger_level="hungry",
            sleep="彻底清醒",
            sleep_level="awake",
            busy="在客厅闲着",
            busy_level="free",
        )
        app.role_state.write(context, data=payload, expected_revision=0, operation_key="seed-levels")

        projected = app.role_state.read(context)
        self.assertEqual(projected["values"]["health_level"], "well")
        self.assertEqual(projected["values"]["hunger_level"], "hungry")
        self.assertEqual(projected["values"]["sleep_level"], "awake")
        self.assertEqual(projected["values"]["busy_level"], "free")

        # Disabling health/hunger/busy hides both the narrative and the structured level
        self.configure(manager, health_enabled=False, hunger_enabled=False, busy_enabled=False)
        hidden = app.role_state.read(context)
        self.assertIsNone(hidden["values"]["health"])
        self.assertNotIn("health_level", hidden["values"])
        self.assertIsNone(hidden["values"]["hunger"])
        self.assertNotIn("hunger_level", hidden["values"])
        self.assertIsNone(hidden["values"]["busy"])
        self.assertNotIn("busy_level", hidden["values"])
        self.assertEqual(hidden["values"]["sleep_level"], "awake")

        # Re-enabling restores projected structured levels without re-running the LLM
        self.configure(manager, health_enabled=True, hunger_enabled=True, busy_enabled=True)
        restored = app.role_state.read(context)
        self.assertEqual(restored["values"]["health_level"], "well")
        self.assertEqual(restored["values"]["hunger_level"], "hungry")
        self.assertEqual(restored["values"]["busy_level"], "free")

    async def test_maintenance_round_sanitizes_and_commits_optional_status_levels(self):
        raw_output = encode(state_data(
            energy=84,
            health="睡饱后浑身暖和舒展，手腕和指节都很松快，没有什么酸沉或病痛不适",
            health_level="good",
            hunger="快到午饭点了，胃里略空",
            hunger_level="mild_hunger",
            sleep="彻底清醒",
            sleep_level="invalid_sleep_tag",
            busy="眼下没什么正事忙",
            busy_level="idle",
        ))
        host, app, context, manager = self.setup_state()
        self.configure(manager, enabled=True, health_enabled=True, hunger_enabled=True,
                       sleep_enabled=True, busy_enabled=True, mood_enabled=False, cycle_enabled=False)
        app.state_maintenance.bind(context, "你是一个作息规律、温和细腻的伙伴。")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE role_state_schedules SET next_at = ?", ((NOW + timedelta(days=1)).isoformat(),))
        await self.start(host)
        self.model_behavior = lambda _env: asyncio.sleep(0, result=answered(raw_output))
        result = await app.state_maintenance.run(context, "你是一个作息规律、温和细腻的伙伴。", "req-levels-1")
        self.assertEqual(result["state"], "succeeded")

        projected = app.role_state.read(context)
        self.assertEqual(projected["values"]["health_level"], "well")
        self.assertEqual(projected["values"]["hunger_level"], "hungry")
        self.assertEqual(projected["values"]["busy_level"], "free")
        # Invalid sleep_level was sanitized away without failing the maintenance round
        self.assertNotIn("sleep_level", projected["values"])
        # Verify status API exposes levels directly to the dashboard sidebar
        api_status = manager.status(self.core).unwrap()
        self.assertEqual(api_status["state"]["values"]["health_level"], "well")
        self.assertEqual(api_status["state"]["values"]["busy_level"], "free")


if __name__ == "__main__":
    unittest.main()
