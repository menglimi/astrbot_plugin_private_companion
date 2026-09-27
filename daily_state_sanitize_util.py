# -*- coding: utf-8 -*-
"""sanitize_util 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 609 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import re
from .helpers import _safe_int, _single_line
from .planning import evaluate_detail_quality
from .story_authority import story_legacy_sync_operation
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class DailyStateSanitizeUtilMixin:
    """sanitize_util 域（从 DailyStateMixin 拆出）。"""


    async def _await_framework_db_query(
        self,
        key: str,
        factory: Any,
        *,
        timeout: float,
    ) -> Any:
        """Bound a core DB read without cancelling its aiosqlite connection."""
        tasks = getattr(self, "_framework_db_query_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._framework_db_query_tasks = tasks
        task = tasks.get(key)
        if not isinstance(task, asyncio.Task) or task.done():
            result = factory()
            if not inspect.isawaitable(result):
                return result
            task = asyncio.create_task(result, name=f"private-companion-db:{key[:80]}")
            tasks[key] = task

            def _cleanup(done: asyncio.Task, *, query_key: str = key) -> None:
                if tasks.get(query_key) is done:
                    tasks.pop(query_key, None)
                if done.cancelled():
                    return
                try:
                    done.exception()
                except Exception:
                    pass

            task.add_done_callback(_cleanup)
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    def _sanitize_state_variables_social_facts_inplace(self, state_variables: Any, *, field: str = "state_variables") -> bool:
        if not isinstance(state_variables, list):
            return False
        changed = False
        for index, item in enumerate(state_variables):
            if not isinstance(item, dict):
                continue
            for key in ("value", "note"):
                original = _single_line(item.get(key), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(
                    original,
                    field=f"{field}.{index}.{key}",
                )
                if cleaned != original:
                    item[key] = cleaned
                    changed = True
        return changed

    def _sanitize_relationship_text_tree_inplace(self, value: Any, *, field: str) -> bool:
        changed = False
        if isinstance(value, dict):
            for key, item in list(value.items()):
                item_field = f"{field}.{key}" if field else str(key)
                if str(key) in {
                    "raw",
                    "raw_text",
                    "original_text",
                    "prompt",
                    "prompt_text",
                    "response",
                    "response_text",
                }:
                    continue
                if isinstance(item, str):
                    cleaned = self._sanitize_generation_relationship_context(item, source=item_field)
                    if cleaned != item:
                        value[key] = cleaned
                        changed = True
                elif isinstance(item, (dict, list)) and self._sanitize_relationship_text_tree_inplace(
                    item,
                    field=item_field,
                ):
                    changed = True
            return changed
        if isinstance(value, list):
            rebuilt: list[Any] = []
            for index, item in enumerate(value):
                item_field = f"{field}.{index}" if field else str(index)
                if isinstance(item, str):
                    cleaned = self._sanitize_generation_relationship_context(item, source=item_field)
                    if cleaned != item:
                        changed = True
                    if cleaned:
                        rebuilt.append(cleaned)
                else:
                    if isinstance(item, (dict, list)) and self._sanitize_relationship_text_tree_inplace(
                        item,
                        field=item_field,
                    ):
                        changed = True
                    rebuilt.append(item)
            if rebuilt != value:
                value[:] = rebuilt
                changed = True
        return changed

    @story_legacy_sync_operation("daily-state.story-source-sanitize")
    def _cleanup_generated_relationship_history_inplace(self) -> bool:
        """Stop old Bot-authored relationship hallucinations from becoming new evidence."""
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        changed = False
        counts: dict[str, int] = {}

        for key in (
            "daily_state",
            "daily_plan_history",
            "daily_story_plan_history",
            "detail_enhanced_history",
        ):
            value = data.get(key)
            if isinstance(value, (dict, list)) and self._sanitize_relationship_text_tree_inplace(value, field=key):
                changed = True
                counts[key] = counts.get(key, 0) + 1

        for key in ("bot_diaries", "self_meal_log", "proactive_audit_log"):
            records = data.get(key)
            if isinstance(records, list) and self._sanitize_relationship_text_tree_inplace(records, field=key):
                changed = True
                counts[key] = counts.get(key, 0) + 1

        projects = data.get("creative_projects")
        if isinstance(projects, list):
            for index, project in enumerate(projects):
                if not isinstance(project, dict):
                    continue
                source_text = project.get("source_text")
                if not isinstance(source_text, str):
                    continue
                cleaned = self._sanitize_generation_relationship_context(
                    source_text,
                    source=f"creative_projects.{index}.source_text",
                )
                if cleaned != source_text:
                    project["source_text"] = cleaned
                    changed = True
                    counts["creative_projects.source_text"] = counts.get("creative_projects.source_text", 0) + 1

        skill_state = data.get("skill_growth")
        skills = skill_state.get("skills") if isinstance(skill_state, dict) else None
        if isinstance(skills, dict):
            for skill in skills.values():
                if not isinstance(skill, dict):
                    continue
                logs = skill.get("recent_logs")
                if not isinstance(logs, list):
                    continue
                if self._sanitize_relationship_text_tree_inplace(
                    logs,
                    field="skill_growth.recent_logs",
                ):
                    changed = True
                    counts["skill_growth.recent_logs"] = counts.get("skill_growth.recent_logs", 0) + 1

        qzone_state = data.get("qzone_integration")
        if isinstance(qzone_state, dict):
            recent_posts = qzone_state.get("recent_life_publish_texts")
            if isinstance(recent_posts, list) and self._sanitize_relationship_text_tree_inplace(
                recent_posts,
                field="qzone_integration.recent_life_publish_texts",
            ):
                changed = True
                counts["qzone_integration.recent_life_publish_texts"] = 1
            for key, value in list(qzone_state.items()):
                if not isinstance(value, str):
                    continue
                if not (
                    key.endswith("_text")
                    or key.endswith("_draft")
                    or key.endswith("_caption")
                    or key in {"last_publish_recorded_text"}
                ):
                    continue
                cleaned = self._sanitize_generation_relationship_context(
                    value,
                    source=f"qzone_integration.{key}",
                )
                if cleaned != value:
                    qzone_state[key] = cleaned
                    changed = True
                    counts["qzone_integration.text_fields"] = counts.get("qzone_integration.text_fields", 0) + 1

        if changed:
            logger.info("已清理未声明关系的生成历史: %s", counts)
        return changed

    def _sanitize_runtime_social_facts_inplace(self) -> bool:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        changed = False
        daily_plan = data.get("daily_plan")
        if isinstance(daily_plan, dict) and self._sanitize_daily_plan_inplace(daily_plan):
            changed = True
        story_plan = data.get("daily_story_plan")
        if isinstance(story_plan, dict) and self._sanitize_story_plan_social_facts_inplace(story_plan):
            changed = True
        enhanced = data.get("detail_enhanced_segments")
        if isinstance(enhanced, dict) and self._sanitize_detail_enhanced_segments_inplace(enhanced):
            changed = True
        pool = data.get("proactive_candidate_pool")
        if isinstance(pool, list):
            for index, item in enumerate(pool):
                if self._sanitize_proactive_social_fact_fields_inplace(
                    item,
                    field=f"proactive_candidate_pool.{index}",
                ):
                    changed = True
        users = data.get("users")
        if isinstance(users, dict):
            for user_id, user in users.items():
                if self._sanitize_user_proactive_social_facts_inplace(user, field=f"users.{user_id}"):
                    changed = True
        if self._cleanup_generated_relationship_history_inplace():
            changed = True
        if changed:
            data["social_fact_sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _cleanup_framework_meta_leak_records(self) -> bool:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return False
        meta_leak_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if not callable(meta_leak_checker):
            return False

        def has_meta(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return meta_leak_checker(value)
            return meta_leak_checker(str(value))

        def list_item_has_meta(item: Any, fields: tuple[str, ...]) -> bool:
            if isinstance(item, dict):
                return any(has_meta(item.get(field)) for field in fields)
            return has_meta(item)

        changed = False
        removed_counts: dict[str, int] = {}

        def filter_list(owner: dict[str, Any], key: str, fields: tuple[str, ...], *, limit: int | None = None) -> None:
            nonlocal changed
            raw = owner.get(key)
            if not isinstance(raw, list):
                return
            kept = [item for item in raw if not list_item_has_meta(item, fields)]
            if limit is not None:
                kept = kept[-limit:]
            if len(kept) != len(raw):
                owner[key] = kept
                removed_counts[key] = removed_counts.get(key, 0) + len(raw) - len(kept)
                changed = True

        users = data.get("users")
        if isinstance(users, dict):
            for user in users.values():
                if not isinstance(user, dict):
                    continue
                for key in (
                    "last_companion_message",
                    "last_proactive_message",
                    "last_proactive_text",
                    "last_reply_text",
                ):
                    if has_meta(user.get(key)):
                        user[key] = ""
                        removed_counts[key] = removed_counts.get(key, 0) + 1
                        changed = True
                filter_list(user, "recent_proactive_topics", ("text", "signature", "topic", "motive"), limit=12)
                filter_list(user, "recent_reply_topics", ("text", "signature", "topic"), limit=18)
                filter_list(user, "action_consequences", ("text", "summary", "action_summary"), limit=18)
                continuity = user.get("state_continuity")
                if isinstance(continuity, dict):
                    for key in ("last_action_text", "last_reply_text", "last_message_text"):
                        if has_meta(continuity.get(key)):
                            continuity[key] = ""
                            count_key = f"state_continuity.{key}"
                            removed_counts[count_key] = removed_counts.get(count_key, 0) + 1
                            changed = True

        filter_list(data, "proactive_audit_log", ("text_preview", "original_text_preview", "final_text_preview", "text", "note", "topic", "motive", "diagnostic_detail"), limit=120)

        troubleshooting = data.get("troubleshooting_test_results")
        if isinstance(troubleshooting, dict):
            for key, result in list(troubleshooting.items()):
                if list_item_has_meta(result, ("text_preview", "original_text_preview", "final_text_preview", "detail", "error", "diagnostic_detail")):
                    troubleshooting.pop(key, None)
                    removed_counts["troubleshooting_test_results"] = removed_counts.get("troubleshooting_test_results", 0) + 1
                    changed = True

        prompt_root = data.get("recent_prompt_injections")
        if isinstance(prompt_root, dict):
            for kind, items in list(prompt_root.items()):
                if not isinstance(items, list):
                    continue
                kept: list[Any] = []
                removed = 0
                for item in items:
                    item_has_meta = list_item_has_meta(item, ("preview", "content", "title"))
                    if not item_has_meta and isinstance(item, dict):
                        modules = item.get("modules")
                        if isinstance(modules, list):
                            item_has_meta = any(
                                list_item_has_meta(module, ("preview", "content", "title", "key"))
                                for module in modules
                            )
                    if item_has_meta:
                        removed += 1
                        continue
                    kept.append(item)
                if removed:
                    prompt_root[kind] = kept[:8] if kind == "tts" else kept[:5]
                    count_key = f"recent_prompt_injections.{kind}"
                    removed_counts[count_key] = removed_counts.get(count_key, 0) + removed
                    changed = True

        if changed:
            logger.info("已清理框架工具循环摘要污染记录: %s", removed_counts)
        return changed

    def _sanitize_story_plan_social_facts_inplace(self, story_plan: dict[str, Any]) -> bool:
        if not isinstance(story_plan, dict):
            return False
        changed = False
        summary = _single_line(story_plan.get("summary"), 180)
        if summary:
            cleaned = self._sanitize_daily_plan_social_fact_text(summary, field="story_plan.summary")
            if cleaned != summary:
                story_plan["summary"] = cleaned
                changed = True
        if self._sanitize_state_variables_social_facts_inplace(
            story_plan.get("state_variables"),
            field="story_plan.state_variables",
        ):
            changed = True
        for item in story_plan.get("today_events") or []:
            if not isinstance(item, dict):
                continue
            original = _single_line(item.get("event"), 180)
            if not original:
                continue
            cleaned = self._sanitize_daily_plan_social_fact_text(original, field="story_plan.today_events.event")
            if cleaned != original:
                item["event"] = cleaned
                changed = True
        for item in story_plan.get("proactive_events") or []:
            if not isinstance(item, dict):
                continue
            for field in ("topic", "why", "motive", "scene", "impulse"):
                original = _single_line(item.get(field), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"story_plan.proactive_events.{field}")
                if cleaned != original:
                    item[field] = cleaned
                    changed = True
        if changed:
            story_plan["sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    def _sanitize_detail_snapshot_for_segment_inplace(
        self,
        snapshot: dict[str, Any],
        segment: dict[str, Any] | tuple[int, int] | None,
        *,
        field: str = "detail",
    ) -> bool:
        if not isinstance(snapshot, dict):
            return False
        if isinstance(segment, tuple):
            start, end = segment
        elif isinstance(segment, dict):
            start = _safe_int(segment.get("start"), 0)
            end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
            if end <= start:
                end += 24 * 60
        else:
            start = end = None
        duration = end - start if start is not None and end is not None else None
        changed = False

        original_summary = _single_line(snapshot.get("summary"), 180)
        if original_summary:
            summary = self._sanitize_daily_plan_social_fact_text(original_summary, field=f"{field}.summary")
            summary = self._sanitize_schedule_meal_time_wording(summary, start)
            summary = self._sanitize_overlong_schedule_activity(summary, duration)
            if summary != original_summary:
                snapshot["summary"] = summary
                changed = True

        meal_event_minutes: list[int] = []
        for index, item in enumerate(snapshot.get("today_events") or []):
            if not isinstance(item, dict):
                continue
            original = _single_line(item.get("event"), 180)
            if not original:
                continue
            event_start = start
            window = _single_line(item.get("window"), 24)
            window_match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-~—–至]\s*(\d{1,2}:\d{2})\s*", window)
            event_end = None
            if window_match:
                event_start = self._parse_hhmm_to_minutes(window_match.group(1))
                event_end = self._parse_hhmm_to_minutes(window_match.group(2))
                if event_start is not None and event_end is not None:
                    if event_end <= event_start:
                        event_end += 24 * 60
                    if self._schedule_text_is_single_meal_action(original):
                        meal_event_minutes.append(max(1, event_end - event_start))
            cleaned = self._sanitize_daily_plan_social_fact_text(
                original,
                field=f"{field}.today_events.{index}.event",
            )
            cleaned = self._sanitize_schedule_meal_time_wording(cleaned, event_start)
            if cleaned != original:
                item["event"] = cleaned
                changed = True

        presence = snapshot.get("presence_status")
        if isinstance(presence, dict) and duration is not None and duration > 120:
            custom_text = _single_line(presence.get("custom_text") or presence.get("wording"), 28)
            if self._schedule_text_is_single_meal_action(custom_text):
                cap = min(60, max(meal_event_minutes) if meal_event_minutes else 45)
                configured = _safe_int(presence.get("duration_minutes"), cap, minimum=1)
                if configured > cap or not _single_line(presence.get("duration_minutes"), 12):
                    presence["duration_minutes"] = str(cap)
                    changed = True
        return changed

    def _sanitize_detail_enhanced_segments_inplace(self, enhanced: dict[str, Any]) -> bool:
        if not isinstance(enhanced, dict):
            return False
        changed = False
        for key, snapshot in enhanced.items():
            if not isinstance(snapshot, dict):
                continue
            snapshot_changed = False
            if (
                _single_line(snapshot.get("status"), 24) == "generating"
                and not self._detail_enhancement_snapshot_blocks_generation(snapshot)
            ):
                stale_generation_id = _single_line(snapshot.get("generation_id"), 64)
                snapshot["status"] = "failed"
                snapshot["updated_at"] = self._environment_now().strftime("%H:%M")
                snapshot["error"] = _single_line(snapshot.get("error"), 180) or "上次细化生成中断或超时"
                snapshot["retry_after"] = ""
                snapshot["retry_after_ts"] = 0
                snapshot["summary"] = _single_line(snapshot.get("summary"), 120) or "这一段细化生成中断，稍后会自动重试。"
                stored_previous_state = snapshot.get("previous_item_state") if isinstance(snapshot.get("previous_item_state"), dict) else {}
                snapshot.pop("generation_id", None)
                snapshot.pop("previous_item_state", None)
                keyed = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d+):(\d{1,2}:\d{2})", str(key))
                live_plan = self.data.get("daily_plan", {})
                live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                if keyed and isinstance(live_items, list):
                    index = int(keyed.group(2))
                    live_item = live_items[index] if 0 <= index < len(live_items) and isinstance(live_items[index], dict) else None
                    if isinstance(live_item, dict) and _single_line(live_item.get("_detail_generation_id"), 64) == stale_generation_id:
                        if stored_previous_state:
                            for field, state in stored_previous_state.items():
                                if not isinstance(state, dict):
                                    continue
                                if bool(state.get("existed")):
                                    live_item[field] = state.get("value")
                                else:
                                    live_item.pop(field, None)
                        else:
                            live_item.pop("_detail_generation_id", None)
                changed = True
                snapshot_changed = True
            bounds = self._detail_segment_bounds_for_snapshot_key(str(key))
            if bounds and not isinstance(snapshot.get("quality"), dict):
                snapshot["quality"] = evaluate_detail_quality(
                    self,
                    snapshot,
                    {"start": bounds[0], "end": bounds[1], "item": {}},
                )
                changed = True
                snapshot_changed = True
            if self._sanitize_detail_snapshot_for_segment_inplace(
                snapshot,
                bounds,
                field=f"detail_enhanced_segments.{key}",
            ):
                changed = True
                snapshot_changed = True
            summary = _single_line(snapshot.get("summary"), 180)
            if summary:
                cleaned = self._sanitize_daily_plan_social_fact_text(summary, field=f"detail_enhanced_segments.{key}.summary")
                if cleaned != summary:
                    snapshot["summary"] = cleaned
                    changed = True
                    snapshot_changed = True
            if self._sanitize_state_variables_social_facts_inplace(
                snapshot.get("state_variables"),
                field=f"detail_enhanced_segments.{key}.state_variables",
            ):
                changed = True
                snapshot_changed = True
            for item in snapshot.get("today_events") or []:
                if not isinstance(item, dict):
                    continue
                original = _single_line(item.get("event"), 180)
                if not original:
                    continue
                cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"detail_enhanced_segments.{key}.today_events.event")
                if cleaned != original:
                    item["event"] = cleaned
                    changed = True
                    snapshot_changed = True
            for item in snapshot.get("proactive_events") or []:
                if not isinstance(item, dict):
                    continue
                for field in ("topic", "why", "motive", "scene", "impulse"):
                    original = _single_line(item.get(field), 180)
                    if not original:
                        continue
                    cleaned = self._sanitize_daily_plan_social_fact_text(original, field=f"detail_enhanced_segments.{key}.proactive_events.{field}")
                    if cleaned != original:
                        item[field] = cleaned
                        changed = True
                        snapshot_changed = True
            if snapshot_changed and snapshot.get("status") == "done":
                snapshot["coverage_repair_done"] = True
                snapshot["social_fact_sanitized_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M:%S")
        return changed

    @staticmethod
    def _strip_json_payload_comments(text: str) -> str:
        result: list[str] = []
        index = 0
        in_string = False
        quote_char = ""
        escaped = False
        while index < len(text):
            char = text[index]
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if in_string:
                result.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote_char:
                    in_string = False
                    quote_char = ""
                index += 1
                continue
            if char in {'"', "'"}:
                in_string = True
                quote_char = char
                result.append(char)
                index += 1
                continue
            if char == "/" and nxt == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if char == "/" and nxt == "*":
                index += 2
                while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                    index += 1
                index = min(len(text), index + 2)
                continue
            result.append(char)
            index += 1
        return "".join(result)

    def _repair_json_payload(self, text: str) -> str:
        repaired = str(text or "").strip()
        repaired = repaired.replace("\ufeff", "")
        repaired = repaired.replace("“", '"').replace("”", '"')
        repaired = repaired.replace("‘", "'").replace("’", "'")
        repaired = self._strip_json_payload_comments(repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return repaired.strip()

    def _extract_json_payload(self, raw_text: str) -> Any:
        text = str(raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        object_start, object_end = text.find("{"), text.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(text[object_start : object_end + 1])
        array_start, array_end = text.find("["), text.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(text[array_start : array_end + 1])
        seen_candidates: set[str] = set()
        for candidate in candidates:
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                repaired = self._repair_json_payload(candidate)
                if repaired and repaired not in seen_candidates:
                    seen_candidates.add(repaired)
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError:
                        pass
                    try:
                        parsed = ast.literal_eval(repaired)
                    except (SyntaxError, ValueError):
                        parsed = None
                    if isinstance(parsed, (dict, list)):
                        return parsed
        return None
