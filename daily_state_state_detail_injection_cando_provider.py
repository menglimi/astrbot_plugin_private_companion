# -*- coding: utf-8 -*-
"""DailyStateStateDetailInjectionCandoProviderMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 365 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts
from .daily_state_state_shared import Any
from .daily_state_state_shared import CURRENT_MODEL_REPLACEMENT_SOURCES
from .daily_state_state_shared import Iterable
from .daily_state_state_shared import PromptRenderMode
from .daily_state_state_shared import PromptSection
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import find_route
from .daily_state_state_shared import prompt_section
from .daily_state_state_shared import re
from .daily_state_state_shared import render_prompt_sections
from .daily_state_state_shared import scope_allows



class DailyStateStateDetailInjectionCandoProviderMixin:
    """DailyStateStateDetailInjectionCandoProviderMixin（从 DailyStateStateMixin 拆出）。"""


    def _format_detail_injection_prompt_section(self) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="detail.injection",
                title="Bot 模拟当前片段",
                source="daily_state",
                content=content,
            )

        snapshot = self._current_story_plan_snapshot()
        if not snapshot:
            schedule_context = self._format_schedule_context_for_prompt()
            if not schedule_context:
                return build_section()
            body = (
                "附近的日程只作 Bot 的拟人化轻量背景，不是用户事实，也不要当成正在逐字发生的现实事件。\n"
                "当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作优先于本段日程；"
                "日程只能补足空白，不能把这些已发生的状态恢复成旧值。\n"
                f"{schedule_context}"
            )
            return build_section(body)
        lines = [
            "这是 Bot 自身的拟人化片段素材，不是用户事实/现实证据；不要写进长期记忆，用户没问就不要复述。",
            "优先级：当前会话中已明确发生且尚未撤销的换装、地点、携带物和动作 > 用户有效介入 > 当前真实时段 > 本段日程及预设素材。"
            "日程、旧摘要和 state_variables 只能补足未指定信息，不能把已经发生的服装、地点、携带物或动作复原成旧值。",
        ]
        primary_parts = []
        if snapshot.get("summary"):
            primary_parts.append(snapshot["summary"])
        if snapshot.get("event"):
            primary_parts.append(snapshot["event"])
        if primary_parts:
            lines.append("，".join(_single_line(part, 140) for part in primary_parts if _single_line(part, 140)))
        secondary_parts = []
        if snapshot.get("scene"):
            secondary_parts.append(snapshot["scene"])
        if snapshot.get("impulse"):
            secondary_parts.append(f"心里有点{snapshot['impulse']}")
        if secondary_parts:
            lines.append("这一小段像" + "，".join(_single_line(part, 80) for part in secondary_parts if _single_line(part, 80)) + "。")
        segment = self._current_detail_segment_for_update()
        enhanced = self.data.get("detail_enhanced_segments", {})
        detail_snapshot = None
        if isinstance(segment, dict) and isinstance(enhanced, dict):
            detail_snapshot = enhanced.get(str(segment.get("key") or ""))
        if isinstance(detail_snapshot, dict):
            state_variables = detail_snapshot.get("state_variables", [])
            if isinstance(state_variables, list) and state_variables:
                variable_texts = []
                roleplay_state_names = {
                    "情绪",
                    "心情",
                    "体力",
                    "精力",
                    "能量",
                    "心理能量",
                    "睡眠",
                    "睡意",
                    "梦境",
                    "健康",
                    "身体",
                    "饥饿",
                    "饥饿感",
                    "胃口",
                    "周期",
                    "生理期",
                    "等待回复",
                    "等回复",
                    "是否等待回复",
                }

                def _natural_detail_variable(name: str, value: str, note: str = "") -> str:
                    text = f"{name}是{value}"
                    if note:
                        text += f"，{note}"
                    return text

                for variable in state_variables[:6]:
                    if not isinstance(variable, dict):
                        continue
                    name = _single_line(variable.get("name"), 24)
                    value = _single_line(variable.get("value"), 50)
                    note = _single_line(variable.get("note"), 60)
                    if name in roleplay_state_names:
                        continue
                    if name and value:
                        variable_texts.append(_natural_detail_variable(name, value, note))
                if variable_texts:
                    lines.append("细节上，" + "；".join(variable_texts[:3]) + "。")
            interaction_updates = detail_snapshot.get("interaction_updates", [])
            if isinstance(interaction_updates, list) and interaction_updates:
                update_lines = []
                for update in interaction_updates[-3:]:
                    if not isinstance(update, dict):
                        continue
                    if _single_line(update.get("source_role"), 20) != "owner":
                        continue
                    reaction = _single_line(update.get("reaction"), 90)
                    state_updates = update.get("state_updates")
                    state_text = ""
                    if isinstance(state_updates, list) and state_updates:
                        filtered_updates = []
                        for item in state_updates:
                            text = _single_line(item, 50)
                            if not text:
                                continue
                            if any(name and name in text for name in roleplay_state_names):
                                continue
                            filtered_updates.append(text)
                        state_text = "；".join(filtered_updates)
                    pieces = [part for part in (reaction, state_text) if part]
                    if pieces:
                        update_lines.append("，".join(pieces))
                if update_lines:
                    lines.append("刚刚的介入：" + "；".join(update_lines) + "。")
        body = "\n".join(lines)
        return build_section(body)

    def _format_detail_injection(self) -> str:
        """Render the detail section for the diagnostic prompt preview."""

        return render_prompt_sections(
            [self._format_detail_injection_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_remaining(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_condition_started(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {self._format_duration_brief(elapsed)}）"

    def _format_remaining_for_prompt(self, end_ts: Any) -> str:
        seconds = _safe_float(end_ts, 0) - _now_ts()
        if seconds <= 0:
            return "已结束"
        if seconds < 3600:
            minutes = max(1, int(seconds // 60))
            bucket = max(5, int(round(minutes / 5) * 5))
            return f"约{bucket}分钟"
        if seconds < 86400:
            hours = max(1, int(round(seconds / 3600)))
            return f"约{hours}小时"
        return f"约{max(1, int(round(seconds / 86400)))}天"

    def _format_condition_started_for_prompt(self, start_ts: Any) -> str:
        ts = _safe_float(start_ts, 0)
        if ts <= 0:
            return "未知"
        dt = self._environment_fromtimestamp(ts)
        elapsed = max(0.0, _now_ts() - ts)
        if elapsed < 3600:
            minutes = max(1, int(elapsed // 60))
            elapsed_text = f"约{max(5, int(round(minutes / 5) * 5))}分钟"
        elif elapsed < 86400:
            elapsed_text = f"约{max(1, int(round(elapsed / 3600)))}小时"
        else:
            elapsed_text = f"约{max(1, int(round(elapsed / 86400)))}天"
        return f"{dt.strftime('%m-%d %H:%M')}（已持续 {elapsed_text}）"

    def _format_duration_brief(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        if seconds < 60:
            return f"{max(1, int(seconds))} 秒"
        if seconds < 3600:
            return f"{max(1, int(seconds // 60))} 分钟"
        if seconds < 86400:
            return f"{int(seconds // 3600)} 小时"
        return f"{int(seconds // 86400)} 天"

    def _format_suspended_summary(self, user: dict[str, Any]) -> str:
        raw = user.get("suspended_proactive")
        if not isinstance(raw, dict) or not raw.get("active"):
            return "悬着的话头：无"
        opener = _single_line(raw.get("opener_text"), 40) or "已先叫了一声"
        if raw.get("resume_ready"):
            return f"悬着的话头：等到用户回头了（{opener}）"
        due_at = _safe_float(raw.get("complaint_after_ts"), 0)
        due_text = self._format_remaining(due_at) if due_at > 0 and not raw.get("complaint_sent") else "已发过后续"
        return f"悬着的话头：还挂着（{opener}｜再等 {due_text}）"

    def _split_can_do_items(self, text: str) -> list[str]:
        raw_parts = re.split(r"[,,、;；\n]+", text)
        items = []
        for part in raw_parts:
            item = _single_line(part, 80)
            if item and item not in items:
                items.append(item)
        return items

    def _add_can_do_items(self, text: str) -> list[str]:
        new_items = self._split_can_do_items(text)
        if not new_items:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            current = []
            self.data["can_do"] = current
        added = []
        existing = {str(item) for item in current}
        for item in new_items:
            if item in existing:
                continue
            current.append(item)
            existing.add(item)
            added.append(item)
        if len(current) > 50:
            del current[:-50]
        return added

    def _remove_can_do_items(self, text: str) -> list[str]:
        targets = self._split_can_do_items(text)
        if not targets:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed = []
        kept = []
        for item in current:
            item_text = str(item)
            if any(target in item_text or item_text in target for target in targets):
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    def _remove_can_do_targets(self, targets: Iterable[Any]) -> list[str]:
        """Remove can_do fragments that are clearly the same as blocked proactive material."""
        normalized_targets: list[str] = []
        target_signatures: set[str] = set()
        for raw in targets or []:
            text = _single_line(raw, 160)
            if not text:
                continue
            for part in self._split_can_do_items(text) or [text]:
                part_text = _single_line(part, 120)
                if len(part_text) < 3 or part_text in normalized_targets:
                    continue
                normalized_targets.append(part_text)
                signature = self._proactive_topic_signature(part_text)
                if signature:
                    target_signatures.add(signature)
        if not normalized_targets and not target_signatures:
            return []
        current = self.data.setdefault("can_do", [])
        if not isinstance(current, list):
            self.data["can_do"] = []
            return []
        removed: list[str] = []
        kept: list[Any] = []
        for item in current:
            item_text = _single_line(item, 120)
            if not item_text:
                continue
            item_signature = self._proactive_topic_signature(item_text)
            matched = any(
                target in item_text or item_text in target
                for target in normalized_targets
                if len(target) >= 3 and len(item_text) >= 3
            )
            if not matched and item_signature:
                matched = any(self._topic_signature_similar(item_signature, sig) for sig in target_signatures)
            if matched:
                removed.append(item_text)
            else:
                kept.append(item)
        self.data["can_do"] = kept
        return removed

    def _provider_matches_deepseek(self, provider_id: str) -> bool:
        safe_id = str(provider_id or "").strip()
        if not safe_id:
            return False
        parts = [safe_id]
        provider = None
        getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
        if callable(getter):
            try:
                provider = getter(safe_id)
            except Exception:
                provider = None
        if provider is not None:
            parts.extend(
                str(value or "")
                for value in (
                    getattr(provider, "name", ""),
                    getattr(provider, "display_name", ""),
                    provider.__class__.__name__,
                )
            )
            config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
            fields = (
                "id", "provider_id", "name", "display_name", "label", "title", "provider", "type",
                "provider_type", "model", "model_name", "api_model", "model_id", "api_base", "base_url",
                "api_base_url", "api_url", "endpoint", "url",
            )
            for field in fields:
                value = config.get(field, "") if isinstance(config, dict) else getattr(config, field, "")
                if value:
                    parts.append(str(value))
        keywords = [
            item.strip().lower()
            for item in re.split(r"[,，;；\n]+", str(getattr(self, "deepseek_peak_match_keywords", "") or ""))
            if item.strip()
        ] or ["deepseek", "深度求索"]
        haystack = " ".join(parts).lower()
        return any(keyword in haystack for keyword in keywords)

    def _task_provider(
        self,
        *provider_ids: str | None,
        allow_replacement: bool = True,
    ) -> str:
        for provider_id in provider_ids:
            value = str(provider_id or "").strip()
            if value:
                if not allow_replacement:
                    return value
                routed = value
                if scope_allows(getattr(self, "model_replacement_scope", "plugin"), "plugin"):
                    sources = CURRENT_MODEL_REPLACEMENT_SOURCES.get(())
                    rules = getattr(self, "model_replacement_rules", None)
                    if sources and isinstance(rules, list):
                        match = find_route(rules, sources)
                        if match is not None:
                            candidate = str(match.rule.provider_id or "").strip()
                            getter = getattr(getattr(self, "context", None), "get_provider_by_id", None)
                            if candidate and callable(getter):
                                try:
                                    if getter(candidate) is not None:
                                        routed = candidate
                                except Exception:
                                    pass
                return self._apply_deepseek_peak_replacement(routed, target="plugin")
        return ""

    def _filter_snapshot_items_to_segment(
        self,
        raw_items: Any,
        segment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list) or not isinstance(segment, dict):
            return []
        start = _safe_int(segment.get("start"), 0)
        end = _safe_int(segment.get("end"), self._segment_end_minutes(start, segment.get("item")))
        if end <= start:
            end += 24 * 60
        kept: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if self._normalize_schedule_lifecycle_status(item.get("lifecycle_status")) == "cancelled":
                continue
            item_start, item_end = self._parse_window_minutes(str(item.get("window") or ""))
            if item_start is None or item_end is None:
                continue
            candidates = [(item_start, item_end)]
            if item_end < item_start:
                candidates = [(item_start, item_end + 24 * 60)]
            if item_start < start and end > 24 * 60:
                candidates.append((item_start + 24 * 60, item_end + 24 * 60))
            if any(candidate_start >= start and candidate_end <= end for candidate_start, candidate_end in candidates):
                kept.append(item)
        return kept
