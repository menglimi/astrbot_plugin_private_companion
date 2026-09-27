# -*- coding: utf-8 -*-
"""CoreStoreNewStoreDefaultsMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 461 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations
from .core_store_shared import Any
from .core_store_shared import DATA_VERSION
from .core_store_shared import DEFAULT_CLOSED_REPAIR_OPERATION_ID
from .core_store_shared import _now_ts
from .core_store_shared import _safe_int
from .core_store_shared import _single_line
from .core_store_shared import empty_person_store
from .core_store_shared import ensure_person_store
from .core_store_shared import migrate_legacy_capabilities
from .core_store_shared import repair_default_closed_capabilities
from .core_store_shared import time



class CoreStoreNewStoreDefaultsMixin:
    """CoreStoreNewStoreDefaultsMixin（从 CoreStoreMixin 拆出）。"""


    def _new_store(self) -> dict[str, Any]:
        return {
            "version": DATA_VERSION,
            "primary_store_ownership": {
                "schema_version": 1,
                "owner_persona_id": "",
                "active_persona_id": "",
                "status": "unattributed",
                "history": [],
            },
            "users": {},
            "private_user_alias_merge_backups": {},
            "groups": {},
            "persona_routing_warnings": {"schema_version": 2, "items": []},
            "hdsi_trial_observations": {
                "schema_version": 1,
                "events": [],
                "metrics": {},
            },
            "hdsi_event_ledger": {
                "schema_version": 1,
                "events": [],
            },
            "hdsi_actor_state": {},
            "daily_plan": {},
            "daily_plan_history": [],
            "agenda_version": 1,
            "agenda_contract_version": 0,
            "observed_activities": [],
            "calendar_version": 1,
            "calendar_events": [],
            "calendar_rules": [],
            "calendar_exceptions": [],
            "calendar_candidates": [],
            "calendar_observations": [],
            "place_cognitive_maps": {},
            "reality_touch_outputs": {},
            "window_snapshots": [],
            "agenda_reconciliation_history": [],
            "daily_state": {},
            "daily_weather": {},
            "state_conditions": [],
            "state_generated_day": "",
            "body_cycle_state": {},
            "body_cycle_strategy_mode": "",
            "bot_diaries": [],
            "dream_fragments": [],
            "daily_dream": {},
            "diary_generated_day": "",
            "daily_diary_deleted_days": [],
            "daily_diary_delete_revision": 0,
            "daily_diary_failed_day": "",
            "daily_diary_failed_at": 0,
            "daily_diary_last_error": "",
            "daily_diary_postprocess_error": "",
            "daily_outfit_photo": {},
            "daily_outfit_history": [],
            "dialogue_outfit_override": {},
            "recent_photo_generations": [],
            "recent_photo_continuity": {},
            "daily_story_plan": {},
            "daily_story_plan_history": [],
            "bot_personal_outbox": [],
            "bot_personal_archive_revisions": {},
            "skill_growth": {},
            "detail_enhanced_day": "",
            "detail_enhanced_segments": {},
            "detail_enhanced_history": [],
            "schedule_adjustments": [],
            "yesterday_conversation_summary": {},
            "can_do": [],
            "important_dates": [],
            "qq_presence_state": {},
            "token_usage": {},
            "bilibili_integration": {},
            "news_integration": {},
            "web_exploration": {},
            "qzone_integration": {},
            "reading_archive_integration": {},
            "bookshelf_items": [],
            "bookshelf_secret": {},
            "bookshelf_store_revision": 0,
            "memo_notes": [],
            "creative_projects": [],
            "creative_memory_pool": [],
            "proactive_candidate_pool": [],
            "proactive_runtime": {},
            "proactive_review_runtime": {},
            "proactive_audit_log": [],
            "passive_no_reply_records": {},
            "external_event_pool": [],
            "external_event_self_link_cache": {},
            "external_proactive_abilities": {},
            "boundary_feedback_reports": [],
            "boundary_feedback_vent_history": [],
            "worldbook_entries": [],
            "worldbook_member_profiles": {},
            "worldbook_group_profiles": {},
            # Visual references are kept separate from the legacy 24-item catalog.
            "photo_reference_assets": [],
            "worldbook_import_state": {},
            "runtime_settings": {},
            "manual_diagnosis_pending_config": {},
            "manual_diagnosis_recent_context": {},
            "inbound_debounce_stats": {},
            "smart_message_debounce": {},
            "group_llm_reply_blocks": {},
            "reaction_expression_group_states": {},
            "cache_metrics": {},
            "_req041_memory_scope_state": {},
            "persona_lifecycle": {
                "generation": 1,
                "reset_at": 0,
                "previous_backup": "",
            },
            "balance_awareness": {},
            "qweather_location": {},
            "weather_alerts": {},
            "weather_alert_awareness": {},
            "body_monitor_integration": {},
            "environment_change_awareness": {},
            "personal_goal_state": {},
            "personal_goals": [],
            "food_menu": {},
            "daily_review_reports": [],
            "daily_review_active_guidance": {},
            "daily_review_last_attempt": {},
            "daily_review_completed_day": "",
            "daily_review_case_audit": [],
            "troubleshooting_test_results": {},
            "troubleshooting_suppressed_warning_types": [],
            "expression_learning_runtime": {},
            "expression_voice_profile": {},
            "extension_migration_notice_preferences": {},
            "hunger_window_attempts": {},
            "last_food_state_feedback_at": 0,
            "last_food_state_feedback_text": "",
            "live_stream_companion": {},
            "pending_atrelay_receipts": [],
            "pending_atrelay_requests": {},
            "personality_iteration_auto_tune": {},
            "private_image_vision_cache": {},
            "private_image_visual_provider_state": {},
            "proactive_only_temp_unlocks": {},
            "photo_generation_scope_attempts": {},
            "photo_reference_feedback": [],
            "reality_touch": {},
            "recent_atrelay_contexts": [],
            "recent_prompt_injection_events": [],
            "recent_prompt_injections": {},
            "social_fact_sanitized_at": "",
            "screen_diary_context": {},
            "self_meal_log": [],
            "setup_guide_completed_at": "",
            "setup_guide_completed_version": "",
            "web_search_runtime": {},
            "unified_person": empty_person_store(),
            "req036_capability_migration": {},
            "_req041_expression_promotion_operations": {},
            "_req041_group_reset_sagas": {},
            "_req041_private_memory": {
                "schema": "req041.person_private_memory.v1",
                "records": {},
            },
            "_req041_persona_expression_profile": {},
            "_req041_persona_reset_saga": {},
            "atrelay_send_log": [],
            "worldbook_deleted_member_ids": [],
            "worldbook_deleted_group_ids": [],
            "proactive_candidate_repeat_sanitized_at": "",
        }

    @staticmethod
    def _ensure_store_defaults(data: dict[str, Any]) -> dict[str, Any]:
        data.setdefault("version", DATA_VERSION)
        ownership = data.get("primary_store_ownership")
        if not isinstance(ownership, dict):
            data["primary_store_ownership"] = {
                "schema_version": 1,
                "owner_persona_id": "",
                "active_persona_id": "",
                "status": "unattributed",
                "history": [],
            }
        else:
            ownership.setdefault("schema_version", 1)
            ownership.setdefault("owner_persona_id", "")
            ownership.setdefault("active_persona_id", "")
            ownership.setdefault("status", "unattributed")
            if not isinstance(ownership.get("history"), list):
                ownership["history"] = []
        data.setdefault("users", {})
        users = data.get("users")
        if isinstance(users, dict):
            for raw_user in users.values():
                if isinstance(raw_user, dict) and "unanswered_proactive_count" not in raw_user:
                    raw_user["unanswered_proactive_count"] = _safe_int(
                        raw_user.get("ignored_streak"),
                        0,
                        0,
                        1000,
                    )
        data.setdefault("private_user_alias_merge_backups", {})
        data.setdefault("groups", {})
        data.setdefault("persona_routing_warnings", {"schema_version": 2, "items": []})
        trial_observations = data.setdefault(
            "hdsi_trial_observations",
            {"schema_version": 1, "events": [], "metrics": {}},
        )
        if not isinstance(trial_observations, dict):
            trial_observations = {
                "schema_version": 1,
                "events": [],
                "metrics": {},
            }
            data["hdsi_trial_observations"] = trial_observations
        trial_observations.setdefault("schema_version", 1)
        if not isinstance(trial_observations.get("events"), list):
            trial_observations["events"] = []
        else:
            del trial_observations["events"][:-200]
        if not isinstance(trial_observations.get("metrics"), dict):
            trial_observations["metrics"] = {}
        event_ledger = data.setdefault(
            "hdsi_event_ledger",
            {"schema_version": 1, "events": []},
        )
        if not isinstance(event_ledger, dict):
            event_ledger = {"schema_version": 1, "events": []}
            data["hdsi_event_ledger"] = event_ledger
        event_ledger.setdefault("schema_version", 1)
        if not isinstance(event_ledger.get("events"), list):
            event_ledger["events"] = []
        else:
            # Keep an upgrade from loading an unbounded trial ledger into
            # memory. Detailed entries are observational and disposable.
            del event_ledger["events"][:-320]
        actor_state = data.setdefault("hdsi_actor_state", {})
        if not isinstance(actor_state, dict):
            data["hdsi_actor_state"] = {}
        else:
            trial_modes = {"hdsi_shadow", "hdsi_active"}
            now = time.time()
            cutoff = now - 30 * 24 * 60 * 60
            trial_items = []
            for actor_key, actor in list(actor_state.items()):
                if not isinstance(actor, dict):
                    actor_state.pop(actor_key, None)
                    continue
                windows = actor.get("windows")
                if isinstance(windows, list):
                    actor["windows"] = windows[:24]
                mode = str(actor.get("mode", "") or "")
                if mode in trial_modes:
                    last = actor.get("last_event_at", 0)
                    try:
                        last = max(0.0, float(last or 0.0))
                    except (TypeError, ValueError, OverflowError):
                        last = 0.0
                    if last > 0 and last < cutoff:
                        actor_state.pop(actor_key, None)
                        continue
                    trial_items.append((actor_key, last))
            if len(trial_items) > 128:
                trial_items.sort(key=lambda item: item[1], reverse=True)
                for actor_key, _ in trial_items[128:]:
                    actor_state.pop(actor_key, None)
        data.setdefault("daily_plan", {})
        data.setdefault("daily_plan_history", [])
        data.setdefault("agenda_version", 1)
        data.setdefault("agenda_contract_version", 0)
        data.setdefault("observed_activities", [])
        data.setdefault("calendar_version", 1)
        data.setdefault("calendar_events", [])
        data.setdefault("calendar_rules", [])
        data.setdefault("calendar_exceptions", [])
        data.setdefault("calendar_candidates", [])
        data.setdefault("calendar_observations", [])
        data.setdefault("place_cognitive_maps", {})
        data.setdefault("reality_touch_outputs", {})
        data.setdefault("window_snapshots", [])
        data.setdefault("agenda_reconciliation_history", [])
        data.setdefault("daily_state", {})
        data.setdefault("daily_weather", {})
        data.setdefault("state_conditions", [])
        data.setdefault("state_generated_day", "")
        data.setdefault("body_cycle_state", {})
        data.setdefault("body_cycle_strategy_mode", "")
        data.setdefault("bot_diaries", [])
        data.setdefault("dream_fragments", [])
        data.setdefault("daily_dream", {})
        data.setdefault("diary_generated_day", "")
        data.setdefault("daily_diary_deleted_days", [])
        data.setdefault("daily_diary_delete_revision", 0)
        data.setdefault("daily_diary_failed_day", "")
        data.setdefault("daily_diary_failed_at", 0)
        data.setdefault("daily_diary_last_error", "")
        data.setdefault("daily_diary_postprocess_error", "")
        data.setdefault("daily_outfit_photo", {})
        data.setdefault("daily_outfit_history", [])
        data.setdefault("dialogue_outfit_override", {})
        data.setdefault("recent_photo_generations", [])
        data.setdefault("recent_photo_continuity", {})
        data.setdefault("daily_story_plan", {})
        data.setdefault("daily_story_plan_history", [])
        data.setdefault("bot_personal_outbox", [])
        data.setdefault("bot_personal_archive_revisions", {})
        data.setdefault("skill_growth", {})
        data.setdefault("detail_enhanced_day", "")
        data.setdefault("detail_enhanced_segments", {})
        data.setdefault("detail_enhanced_history", [])
        data.setdefault("schedule_adjustments", [])
        data.setdefault("yesterday_conversation_summary", {})
        data.setdefault("can_do", [])
        data.setdefault("important_dates", [])
        data.setdefault("qq_presence_state", {})
        data.setdefault("token_usage", {})
        data.setdefault("bilibili_integration", {})
        data.setdefault("news_integration", {})
        data.setdefault("web_exploration", {})
        data.setdefault("qzone_integration", {})
        data.setdefault("reading_archive_integration", {})
        data.setdefault("bookshelf_items", [])
        data.setdefault("bookshelf_secret", {})
        data.setdefault("bookshelf_store_revision", 0)
        data.setdefault("memo_notes", [])
        data.setdefault("creative_projects", [])
        data.setdefault("creative_memory_pool", [])
        data.setdefault("proactive_candidate_pool", [])
        data.setdefault("proactive_runtime", {})
        data.setdefault("proactive_review_runtime", {})
        data.setdefault("proactive_audit_log", [])
        data.setdefault("passive_no_reply_records", {})
        data.setdefault("external_event_pool", [])
        data.setdefault("external_event_self_link_cache", {})
        data.setdefault("external_proactive_abilities", {})
        data.setdefault("boundary_feedback_reports", [])
        data.setdefault("boundary_feedback_vent_history", [])
        data.setdefault("worldbook_entries", [])
        data.setdefault("worldbook_member_profiles", {})
        data.setdefault("worldbook_group_profiles", {})
        data.setdefault("photo_reference_assets", [])
        data.setdefault("worldbook_deleted_member_ids", [])
        data.setdefault("worldbook_deleted_group_ids", [])
        data.setdefault("worldbook_import_state", {})
        data.setdefault("runtime_settings", {})
        data.setdefault("manual_diagnosis_pending_config", {})
        data.setdefault("manual_diagnosis_recent_context", {})
        data.setdefault("atrelay_send_log", [])
        data.setdefault("inbound_debounce_stats", {})
        data.setdefault("smart_message_debounce", {})
        data.setdefault("group_llm_reply_blocks", {})
        data.setdefault("reaction_expression_group_states", {})
        data.setdefault("cache_metrics", {})
        data.setdefault("_req041_memory_scope_state", {})
        lifecycle = data.setdefault("persona_lifecycle", {})
        if not isinstance(lifecycle, dict):
            lifecycle = {}
            data["persona_lifecycle"] = lifecycle
        lifecycle.setdefault("generation", 1)
        lifecycle.setdefault("reset_at", 0)
        lifecycle.setdefault("previous_backup", "")
        data.setdefault("balance_awareness", {})
        data.setdefault("qweather_location", {})
        data.setdefault("weather_alerts", {})
        data.setdefault("weather_alert_awareness", {})
        data.setdefault("body_monitor_integration", {})
        data.setdefault("environment_change_awareness", {})
        data.setdefault("personal_goal_state", {})
        data.setdefault("personal_goals", [])
        data.setdefault("food_menu", {})
        data.setdefault("daily_review_reports", [])
        data.setdefault("daily_review_active_guidance", {})
        data.setdefault("daily_review_last_attempt", {})
        data.setdefault("daily_review_completed_day", "")
        data.setdefault("daily_review_case_audit", [])
        data.setdefault("troubleshooting_test_results", {})
        data.setdefault("troubleshooting_suppressed_warning_types", [])
        data.setdefault("expression_learning_runtime", {})
        data.setdefault("expression_voice_profile", {})
        data.setdefault("extension_migration_notice_preferences", {})
        data.setdefault("hunger_window_attempts", {})
        data.setdefault("last_food_state_feedback_at", 0)
        data.setdefault("last_food_state_feedback_text", "")
        data.setdefault("live_stream_companion", {})
        data.setdefault("pending_atrelay_receipts", [])
        data.setdefault("pending_atrelay_requests", {})
        data.setdefault("personality_iteration_auto_tune", {})
        data.setdefault("private_image_vision_cache", {})
        data.setdefault("private_image_visual_provider_state", {})
        data.setdefault("proactive_only_temp_unlocks", {})
        data.setdefault("photo_generation_scope_attempts", {})
        data.setdefault("photo_reference_feedback", [])
        data.setdefault("reality_touch", {})
        data.setdefault("recent_atrelay_contexts", [])
        data.setdefault("recent_prompt_injection_events", [])
        data.setdefault("recent_prompt_injections", {})
        data.setdefault("social_fact_sanitized_at", "")
        data.setdefault("screen_diary_context", {})
        data.setdefault("self_meal_log", [])
        data.setdefault("setup_guide_completed_at", "")
        data.setdefault("setup_guide_completed_version", "")
        data.setdefault("web_search_runtime", {})
        data.setdefault("req036_capability_migration", {})
        data.setdefault("_req041_expression_promotion_operations", {})
        data.setdefault("_req041_group_reset_sagas", {})
        data.setdefault(
            "_req041_private_memory",
            {"schema": "req041.person_private_memory.v1", "records": {}},
        )
        data.setdefault("_req041_persona_expression_profile", {})
        data.setdefault("_req041_persona_reset_saga", {})
        data.setdefault("proactive_candidate_repeat_sanitized_at", "")
        ensure_person_store(data)
        # Legacy profiles retain their effective durable permissions once.
        # Creation paths below install a separate default-closed state for new
        # identities, so a user starting a DM cannot manufacture authority.
        migrate_legacy_capabilities(
            data,
            operation_id="req036-capability-v1",
            dry_run=False,
        )
        # PR #110 could recreate an already observed legacy identity with a
        # schema-v1 default-closed document.  Reconcile only durable legacy
        # enable evidence; new/automatic and explicitly disabled profiles
        # remain closed.  The operation id is versioned so installs that ran
        # the previous compatibility pass still receive this pass once.
        repair_default_closed_capabilities(
            data,
            operation_id=DEFAULT_CLOSED_REPAIR_OPERATION_ID,
            dry_run=False,
        )
        return data

    @staticmethod
    def _data_dict(data: Any, field: str) -> dict[str, Any]:
        value = data.get(field) if isinstance(data, dict) else None
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _data_list(data: Any, field: str) -> list[Any]:
        value = data.get(field) if isinstance(data, dict) else None
        return value if isinstance(value, list) else []

    @staticmethod
    def _data_str(data: Any, field: str, default: str = "") -> str:
        value = data.get(field) if isinstance(data, dict) else None
        return str(value) if value is not None else default

    def _record_cache_metric(self, namespace: str, *, hit: bool, detail: str = "") -> None:
        name = _single_line(namespace, 80)
        if not name:
            return
        metrics = self.data.setdefault("cache_metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
            self.data["cache_metrics"] = metrics
        item = metrics.setdefault(name, {})
        if not isinstance(item, dict):
            item = {}
            metrics[name] = item
        key = "hits" if hit else "misses"
        item[key] = _safe_int(item.get(key), 0, 0) + 1
        item["last_hit_ts" if hit else "last_miss_ts"] = _now_ts()
        if detail:
            item["last_hit_detail" if hit else "last_miss_detail"] = _single_line(detail, 160)
