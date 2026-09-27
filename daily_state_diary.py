# -*- coding: utf-8 -*-
"""diary 域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（44 个方法 + 0 个模块级名字 + 0 个类级赋值 / 881 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import json
import random
import re
import time
from .dreaming import (
    build_dream_memory_fragments,
    dream_fragment_effective_weight,
    dream_theme_specs,
    extract_weighted_dream_fragments,
    fallback_diary_payload,
    fallback_dream_fragments_for_diary,
    generate_daily_diary,
    generate_enhanced_dream_pick,
    merge_dream_fragment_pool,
    normalize_dream_fragment_item,
    normalize_dream_fragment_pool,
    recent_diary_context,
    recent_diary_tags,
    weighted_unique_fragment_sample,
)
from .helpers import _date_key, _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .planning import format_plan_for_diary, normalize_long_term_events
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _today_key(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_today_key")(*args, **kwargs)


def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)

class DailyStateDiaryMixin:
    """diary 域（从 DailyStateMixin 拆出）。"""


    def _next_detail_due_in_seconds(self, now: float | None = None) -> float | None:
        if not runtime_persona_setting(self, "enable_detail_enhancement", False):
            return None
        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return None
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            enhanced = {}
        segments = self._collect_detail_segments(plan, enhanced)
        if not segments:
            return None
        now_dt = self._environment_fromtimestamp(now or _now_ts())
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return None
        lead = max(0, _safe_int(runtime_persona_setting(self, "detail_enhancement_lead_minutes", 3), 3, 0))
        candidates: list[float] = []
        for segment in segments:
            start = _safe_int(segment.get("start"), 0)
            due_minute = max(0, start - lead)
            if due_minute <= now_minutes:
                return 0.0
            due_dt = datetime.combine(now_dt.date(), datetime.min.time(), tzinfo=now_dt.tzinfo) + timedelta(minutes=due_minute)
            candidates.append(max(0.0, due_dt.timestamp() - (now or _now_ts())))
        if not candidates:
            return None
        return min(candidates)

    async def _ensure_daily_diary(self, force: bool = False) -> dict[str, Any] | None:
        request_started = time.monotonic()
        scope = self._daily_generation_scope()
        force_cache = self._daily_force_result_cache("_daily_diary_force_results_by_scope")
        lock = self._daily_generation_lock("_daily_diary_generation_lock")
        async with lock:
            completed_entry = force_cache.get(scope, {})
            if force and _safe_float(completed_entry.get("completed_at"), 0) >= request_started:
                completed = completed_entry.get("result")
                if isinstance(completed, dict):
                    return completed
            diary = await self._ensure_daily_diary_once(force=force)
            if force and isinstance(diary, dict):
                force_cache[scope] = {
                    "result": diary,
                    "completed_at": time.monotonic(),
                }
            return diary

    async def _ensure_daily_diary_once(self, force: bool = False) -> dict[str, Any] | None:
        if not runtime_persona_setting(self, "enable_daily_diary", True) and not force:
            return None
        request_day = _today_key()
        async with self._data_lock:
            delete_revision_at_start = self._daily_diary_delete_revision()
            if not force and self.data.get("diary_generated_day") == request_day:
                return None
            if not force and self._daily_diary_was_manually_deleted(request_day):
                return None
            if not force and self.data.get("daily_diary_failed_day") == request_day:
                failed_at = _safe_float(self.data.get("daily_diary_failed_at"), 0, 0)
                if failed_at > 0 and _now_ts() - failed_at < 30 * 60:
                    return None
            if not force and not self._is_daily_diary_due():
                return None

        try:
            diary = await self._generate_daily_diary()
        except Exception as exc:
            async with self._data_lock:
                self.data["daily_diary_failed_day"] = request_day
                self.data["daily_diary_failed_at"] = _now_ts()
                self.data["daily_diary_last_error"] = _single_line(exc, 180)
                self._save_data_sync(
                    sections={
                        "daily_diary_failed_day",
                        "daily_diary_failed_at",
                        "daily_diary_last_error",
                    }
                )
            if force:
                raise
            logger.warning(
                "生成今日日记失败,已进入30分钟冷却避免重复请求: %s",
                _single_line(exc, 180),
            )
            return None

        diary_day = _single_line(diary.get("date"), 16) if isinstance(diary, dict) else ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", diary_day):
            diary_day = _today_key()
            if isinstance(diary, dict):
                diary["date"] = diary_day
        memory_payload: dict[str, str] | None = None
        async with self._data_lock:
            deleted_now = (
                self._daily_diary_was_manually_deleted(request_day)
                or self._daily_diary_was_manually_deleted(diary_day)
            )
            deleted_during_generation = (
                deleted_now
                and self._daily_diary_delete_revision() != delete_revision_at_start
            )
            if deleted_now and (not force or deleted_during_generation):
                logger.info(
                    "日记生成期间该日期被手动删除，已丢弃生成结果: request_day=%s diary_day=%s force=%s",
                    request_day,
                    diary_day,
                    force,
                )
                return None
            diaries = self.data.setdefault("bot_diaries", [])
            if isinstance(diaries, dict):
                migrated_diaries: list[Any] = []
                for stored_date, stored_diary in diaries.items():
                    fallback_date = _single_line(stored_date, 64)
                    if isinstance(stored_diary, dict):
                        migrated_diary = deepcopy(stored_diary)
                        if fallback_date and not _single_line(migrated_diary.get("date"), 64):
                            migrated_diary["date"] = fallback_date
                    elif isinstance(stored_diary, str):
                        migrated_diary = {"body": stored_diary}
                        if fallback_date:
                            migrated_diary["date"] = fallback_date
                    else:
                        # Keep uncommon JSON-compatible legacy values recoverable instead of dropping them.
                        migrated_diary = {"legacy_value": deepcopy(stored_diary)}
                        if fallback_date:
                            migrated_diary["date"] = fallback_date
                    migrated_diaries.append(migrated_diary)
                diaries = migrated_diaries
                self.data["bot_diaries"] = diaries
                logger.info(
                    "已无损迁移旧字典日记存储: entries=%s",
                    len(diaries),
                )
            elif not isinstance(diaries, list):
                logger.error(
                    "日记记录结构异常，已保留原数据并放弃写入本次生成结果: storage=%s",
                    type(diaries).__name__,
                )
                return None
            if not force and self.data.get("diary_generated_day") == diary_day:
                return next(
                    (
                        item
                        for item in reversed(diaries)
                        if isinstance(item, dict) and _single_line(item.get("date"), 16) == diary_day
                    ),
                    None,
                )
            if force:
                refreshed: list[Any] = []
                replaced = False
                for item in diaries:
                    if isinstance(item, dict) and _single_line(item.get("date"), 16) == diary_day:
                        if not replaced:
                            refreshed.append(diary)
                            replaced = True
                        continue
                    refreshed.append(item)
                if not replaced:
                    refreshed.append(diary)
                diaries[:] = refreshed
            else:
                diaries.append(diary)
            max_entries = max(1, _safe_int(runtime_persona_setting(self, "max_diary_entries", 14), 14, 1))
            del diaries[:-max_entries]
            # Mark the diary as generated before optional enrichment so a post-process
            # bug cannot make the scheduler call the LLM again and again.
            previous_generated_day = _single_line(self.data.get("diary_generated_day"), 16)
            self.data["diary_generated_day"] = max(previous_generated_day, diary_day)
            deleted_days = self.data.get("daily_diary_deleted_days")
            if isinstance(deleted_days, list):
                self.data["daily_diary_deleted_days"] = [
                    value
                    for value in deleted_days
                    if _single_line(value, 16) != diary_day
                ]
            self.data["daily_diary_failed_day"] = ""
            self.data["daily_diary_failed_at"] = 0
            self.data["daily_diary_last_error"] = ""
            try:
                self.data["dream_fragments"] = self._merge_dream_fragment_pool(
                    diary.get("dream_fragments", []) if isinstance(diary, dict) else []
                )
                self.data["daily_diary_postprocess_error"] = ""
            except Exception as exc:
                self.data["daily_diary_postprocess_error"] = _single_line(exc, 180)
                logger.warning(
                    "今日日记已保存,但梦境碎片合并失败: %s",
                    _single_line(exc, 180),
                )
            dream_fragments = diary.get("dream_fragments", []) if isinstance(diary, dict) else []
            if isinstance(dream_fragments, list) and dream_fragments:
                fragment = dream_fragments[0] if isinstance(dream_fragments[0], dict) else {}
                content = _single_line(fragment.get("content") or fragment.get("text") or fragment.get("dream"), 600)
                if content:
                    memory_payload = {
                        "content": content,
                        "mood": _single_line(fragment.get("mood") or fragment.get("emotion"), 40),
                        "dream_type": _single_line(fragment.get("type") or fragment.get("theme"), 40),
                    }
            story_plan = diary.get("story_plan") if isinstance(diary, dict) else None
            if isinstance(story_plan, dict):
                self.data["daily_story_plan"] = story_plan
            self._save_data_sync(
                sections={
                    "bot_diaries",
                    "diary_generated_day",
                    "daily_diary_deleted_days",
                    "daily_diary_failed_day",
                    "daily_diary_failed_at",
                    "daily_diary_last_error",
                    "daily_diary_postprocess_error",
                    "dream_fragments",
                    "daily_story_plan",
                }
            )
        diary_recorder = getattr(self, "_memory_companion_record_daily_diary", None)
        if callable(diary_recorder):
            try:
                archive_result = await diary_recorder(diary)
            except Exception as exc:
                archive_result = {
                    "ok": False,
                    "state": "degraded",
                    "error_code": type(exc).__name__,
                }
                logger.warning("Bot Personal 日记归档失败: %s", _single_line(exc, 160))
            if isinstance(diary, dict) and isinstance(archive_result, dict):
                async with self._data_lock:
                    diary["memory_archive"] = dict(archive_result)
                    self._save_data_sync(
                        sections={
                            "bot_diaries",
                            "bot_personal_outbox",
                            "bot_personal_archive_revisions",
                        }
                    )
        if memory_payload:
            try:
                await self._memory_companion_record_dream_fragment(**memory_payload)
            except Exception:
                pass
        return diary

    def _daily_diary_was_manually_deleted(self, day: Any) -> bool:
        date_key = _single_line(day, 16)
        if not date_key:
            return False
        deleted_days = self.data.get("daily_diary_deleted_days")
        if isinstance(deleted_days, str):
            deleted_days = [deleted_days]
        if not isinstance(deleted_days, list):
            return False
        return any(_single_line(value, 16) == date_key for value in deleted_days)

    def _daily_diary_delete_revision(self) -> int:
        try:
            return max(0, int(self.data.get("daily_diary_delete_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    def _is_daily_diary_due(self) -> bool:
        diary_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_diary_time", "23:10"))
        if diary_minutes is None:
            diary_minutes = 23 * 60 + 10
        now = self._environment_now()
        return now.hour * 60 + now.minute >= diary_minutes

    def _next_daily_diary_due_in_seconds(self, now: float | None = None) -> float | None:
        """Return the next diary maintenance deadline without creating another timer."""
        if not runtime_persona_setting(self, "enable_daily_diary", True):
            return None
        check_now = _safe_float(now, _now_ts())
        now_dt = self._environment_fromtimestamp(check_now)
        today = now_dt.strftime("%Y-%m-%d")
        if _single_line(self.data.get("diary_generated_day"), 16) == today:
            return None
        if self._daily_diary_was_manually_deleted(today):
            return None

        diary_minutes = self._parse_hhmm_to_minutes(runtime_persona_setting(self, "daily_diary_time", "23:10"))
        if diary_minutes is None:
            diary_minutes = 23 * 60 + 10
        due_dt = datetime.combine(
            now_dt.date(),
            datetime.min.time(),
            tzinfo=now_dt.tzinfo,
        ) + timedelta(minutes=diary_minutes)
        due_at = due_dt.timestamp()

        if _single_line(self.data.get("daily_diary_failed_day"), 16) == today:
            failed_at = _safe_float(self.data.get("daily_diary_failed_at"), 0, 0)
            if failed_at > 0:
                due_at = max(due_at, failed_at + 30 * 60)
        return max(0.0, due_at - check_now)

    async def _generate_daily_diary(self) -> dict[str, Any]:
        await self._ensure_yesterday_conversation_summary()
        return await generate_daily_diary(self)

    def _fallback_diary_payload(self, evidence: list[dict[str, str]] | None = None) -> dict[str, Any]:
        return fallback_diary_payload(self, evidence=evidence)

    def _polish_diary_text(self, text: Any, *, field: str = "body") -> str:
        cleaned = _single_line(text, 900 if field == "body" else 180)
        if not cleaned:
            return ""
        cleaned = re.sub(
            r"状态(?:大概|大约)?(?:是|偏|比较)?([^,，。；;]{1,10})[,，]\s*能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}\s*/\s*100(?:\s*左右)?[,，]?\s*适合[^。；;，,]{0,30}(?:推进|节奏|小事)",
            r"整个人还有点\1,就慢慢把注意力放回眼前的小事",
            cleaned,
        )
        cleaned = re.sub(
            r"今天(?:整体|大概)?偏([^,，。；;]{1,10})[,，]\s*能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}\s*/\s*100(?:\s*左右)?[,，]?\s*适合[^。；;，,]{0,30}(?:推进|节奏|小事)",
            r"今天有点\1,就慢慢把注意力放回眼前的小事",
            cleaned,
        )
        cleaned = re.sub(r"能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}\s*/\s*100(?:\s*左右)?", "精神还有点起伏", cleaned)
        cleaned = re.sub(r"能量(?:大概|大约|大抵)?(?:是|在|停在|约)?\s*\d{1,3}(?:\s*左右)?", "精神还有点起伏", cleaned)
        cleaned = re.sub(r"状态(?:大概|大约)?(?:是|偏|比较)?([^,，。；;]{1,10})", r"整个人有点\1", cleaned)
        cleaned = re.sub(r"今天(?:整体|大概)?偏([^,，。；;]{1,10})", r"今天有点\1", cleaned)
        cleaned = re.sub(r"状态(?:大概|大约)?(?:是|偏|比较)?([^,，。；;]{0,8}),?适合[^。；;，,]{0,20}(?:推进|节奏)", r"整个人有点\1", cleaned)
        cleaned = re.sub(r"今天(?:整体|大概)?偏([^,，。；;]{1,8}),?适合[^。；;，,]{0,20}(?:推进|节奏)", r"今天有点偏\1", cleaned)
        cleaned = re.sub(r"(?:先)?确认了?一下自己的状态", "在床边缓了一会儿", cleaned)
        cleaned = re.sub(r"适合(?:保持|继续)?(?:温和|平稳|稳定)?(?:慢慢)?(?:推进|推着走|节奏)", "可以慢一点来", cleaned)
        cleaned = re.sub(r"平稳推进", "慢慢来", cleaned)
        cleaned = re.sub(r"可分享(?:的)?(?:碎片|句子)[:：]?", "", cleaned)
        cleaned = re.sub(r"(?:主动计划|插件|模型|生成器|内部状态|状态报告)[:：]?", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,；;")
        if field == "share":
            cleaned = cleaned.rstrip("。.!！？")
            if any(marker in cleaned for marker in ("适合", "推进", "状态")) and not any(marker in cleaned for marker in ("醒", "梦", "路", "雨", "风", "杯", "灯", "窗", "课", "饭", "困")):
                cleaned = "今天有点慢半拍,想等遇到新的小事再讲给你听"
            return _single_line(cleaned, 90)
        if field == "summary":
            if any(marker in cleaned for marker in ("精神还有点起伏", "状态", "适合")) and not any(marker in cleaned for marker in ("醒", "梦", "路", "雨", "风", "杯", "灯", "窗", "课", "饭", "困")):
                cleaned = "把手边的一件小事慢慢收好，心里也腾出了一点位置。"
            return _single_line(cleaned, 120)
        return _single_line(cleaned, 520)

    def _polish_diary_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        polished = dict(payload)
        polished["summary"] = self._polish_diary_text(polished.get("summary"), field="summary")
        polished["body"] = self._polish_diary_text(polished.get("body"), field="body")
        polished["share_seed"] = self._polish_diary_text(polished.get("share_seed"), field="share")
        if not polished["summary"]:
            polished["summary"] = "今天留下的具体记录不多"
        if not polished["body"]:
            polished["body"] = "今天没有留下足够具体、可以确认的经历，就先如实记到这里。"
        return polished

    def _generate_fallback_long_term_events(self, state: dict[str, Any]) -> list[dict[str, str]]:
        events = self._generate_state_linked_long_term_events()
        if events:
            return events[:3]
        mood = _single_line(state.get("mood_bias"), 20) if isinstance(state, dict) else "平稳"
        return [
            {
                "title": "今日状态延续",
                "status": f"今天整体偏{mood},适合保持平稳节奏",
                "next_hint": "后续可根据对话自然延伸",
                "phase": "steady",
                "tendency": "状态更可能保持稳定或逐步回升",
            }
        ]

    def _normalize_long_term_events(self, raw_items: Any) -> list[dict[str, str]]:
        return normalize_long_term_events(self, raw_items)

    def _dedupe_long_term_events(self, events: list[dict[str, str]]) -> list[dict[str, str]]:
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in events:
            if not isinstance(item, dict):
                continue
            key = _single_line(item.get("title"), 80)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _generate_state_linked_long_term_events(self) -> list[dict[str, str]]:
        state = self.data.get("daily_state", {})
        if not isinstance(state, dict):
            return []
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        conditions = state.get("conditions", [])
        if not isinstance(conditions, list):
            return []
        candidates: list[dict[str, str]] = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            phase = _single_line(cond.get("phase"), 24)
            kind = _single_line(cond.get("kind"), 24)
            label = _single_line(cond.get("label"), 80)
            if kind == "health" and phase == "mild_discomfort":
                candidates.extend(
                    [
                        {
                            "title": "降低当日活动强度",
                            "status": "轻微不适,适合降低活动强度",
                            "next_hint": "若休息或收到关心反馈,后续更可能进入恢复阶段",
                            "phase": phase,
                            "tendency": "倾向缓慢恢复,也可能延续为轻微不适",
                        },
                        {
                            "title": "观察不适是否缓解",
                            "status": label or "当前仍有轻微不适",
                            "next_hint": "若晚些时候状态回升,主动分享意愿可能增加",
                            "phase": phase,
                            "tendency": "恢复倾向受休息与关心反馈影响",
                        },
                    ]
                )
            elif kind in {"recovery_afterglow", "health_tail"} or phase in {"afterglow", "tail"}:
                candidates.extend(
                    [
                        {
                            "title": "观察是否回到稳定节奏",
                            "status": "状态正在回稳,但仍有轻微波动",
                            "next_hint": "若外部环境和情绪稳定,后续分享意愿可能上升",
                            "phase": phase or kind,
                            "tendency": "倾向回稳,也可能残留轻微尾声",
                        }
                    ]
                )
            if kind == "sleep" and phase == "sleep_debt":
                candidates.append(
                    {
                        "title": "留意睡眠债恢复情况",
                        "status": "精神能量未满,白天反应可能偏慢",
                        "next_hint": "若白天恢复顺利,晚间表达会更轻松；否则保持低强度",
                        "phase": phase,
                        "tendency": "倾向先延续低能量,再逐步回稳",
                    }
                )
            if kind in {"care_warmth", "soft_afterglow"}:
                candidates.append(
                    {
                        "title": "记录关心反馈后的回暖",
                        "status": "收到关心反馈后,语气可能更柔和",
                        "next_hint": "若互动氛围稳定,后续轻分享意愿可能增加",
                        "phase": phase or kind,
                        "tendency": "倾向回稳,小概率保留轻度正向余波",
                    }
                )
        if weather != "暂无天气信息" and any(token in weather for token in ("晴", "阳光", "多云", "晚霞")) and random.random() < 0.45:
            candidates.append(
                {
                    "title": "留意傍晚会不会有值得拍下来的天色",
                    "status": f"天气提供了可用于生活背景的外部线索：{weather}",
                    "next_hint": "若当时情绪稳定,可能提高 photo_text 分享概率",
                    "phase": "weather_bonus",
                    "tendency": "倾向轻量分享,不倾向正式开启长对话",
                }
            )
        picked: list[dict[str, str]] = []
        for item in candidates:
            chance = 0.55
            tendency = str(item.get("tendency") or "")
            if "回稳" in tendency:
                chance = 0.45
            if "拖一阵" in tendency:
                chance = 0.4
            if random.random() < chance:
                picked.append(item)
        return picked[:3]

    def _format_plan_for_diary(self, plan: dict[str, Any]) -> str:
        return format_plan_for_diary(self, plan)

    def _remember_daily_dream_pick(self, dream_pick: tuple[str, str, int, int] | None) -> None:
        if not dream_pick:
            return
        label = _single_line(dream_pick[0], 120)
        if not label:
            return
        payload = getattr(self, "_last_generated_dream_payload", None)
        if not isinstance(payload, dict) or _single_line(payload.get("label"), 120) != label:
            payload = {}
        factors = payload.get("factors", [])
        if not isinstance(factors, list):
            factors = []
        normalized_factors = [_single_line(item, 30) for item in factors[:8] if _single_line(item, 30)]
        if not normalized_factors:
            normalized_factors = self._build_dream_memory_fragments(count=6)
        content = _single_line(payload.get("content"), 1000)
        if not content:
            factor_hint = "、".join(normalized_factors[:4]) or "一些断续的生活碎片"
            content = (
                f"梦里像从{factor_hint}开始,场景没有交代清楚就慢慢换了地方。"
                f"{label}那种感觉一直挂着,中间有些画面接不上,但醒来时还记得自己在梦里顺着它走了一段。"
            )
        self.data["daily_dream"] = {
            "date": _today_key(),
            "label": label,
            "dream_type": _single_line(payload.get("dream_type"), 40) or "碎片梦",
            "factors": normalized_factors,
            "content": content,
            "afterglow": _single_line(payload.get("afterglow"), 220) or label,
            "mood": _single_line(dream_pick[1], 20) or "平稳",
            "energy_delta": _safe_int(dream_pick[2], 0, -30, 20),
            "duration_hours": _safe_int(dream_pick[3], 0, 0, 24),
            "generated_at": self._environment_now().strftime("%Y-%m-%d %H:%M"),
        }

    def _remembered_daily_dream_label(self) -> str:
        raw = self.data.get("daily_dream")
        if not isinstance(raw, dict) or raw.get("date") != _today_key():
            return ""
        label = _single_line(raw.get("label"), 120)
        if label and label != "没有记住梦":
            return label
        return ""

    def _dream_afterglow_strength(self, dream_pick: tuple[str, str, int, int]) -> float:
        mode = str(runtime_persona_setting(self, "dream_afterglow_mode", "auto") or "auto")
        if mode == "轻":
            return 0.7
        if mode == "标准":
            return 1.0
        if mode == "明显":
            return 1.35
        label = str(dream_pick[0] or "")
        energy_delta = abs(int(dream_pick[2] or 0))
        if any(token in label for token in ("不舒服", "追", "黑", "掉下去", "迷路", "醒来后有一点黏着感")):
            return 1.15
        if energy_delta >= 10:
            return 1.1
        if energy_delta <= 2:
            return 0.75
        return 0.95

    def _build_dream_aftertaste_condition(
        self,
        dream_pick: tuple[str, str, int, int],
    ) -> dict[str, Any] | None:
        label = str(dream_pick[0] or "")
        mood = str(dream_pick[1] or "平稳")
        if not label or label == "没有记住梦":
            return None
        strength = self._dream_afterglow_strength(dream_pick)
        if random.random() > min(0.92, 0.45 + strength * 0.2):
            return None
        if any(token in label for token in ("不舒服", "追", "恐怖", "黑", "掉下去", "迷路", "醒来后有一点黏着感")):
            return self._make_condition(
                kind="dream_aftertaste",
                title="梦后的不安残留",
                label="梦里的那点不安还没完全褪干净",
                mood="恍惚" if mood in {"恍惚", "低落", "敏感"} else "敏感",
                energy_delta=-max(2, int(round(4 * strength))),
                duration_hours=max(2, int(round(6 * strength))),
                intensity=min(92, int(50 + 20 * strength)),
                cause=f"梦境余韵：{_single_line(label, 40)}",
                phase="dream_aftertaste",
            )
        if any(token in label for token in ("发光", "柔", "亮色", "温暖", "春梦", "暧昧", "怀旧")):
            return self._make_condition(
                kind="dream_aftertaste",
                title="梦后的余温",
                label="梦里的余温还轻轻黏着一点",
                mood="柔和" if mood not in {"平稳", "中性"} else "安静",
                energy_delta=max(1, int(round(2 * strength))),
                duration_hours=max(2, int(round(5 * strength))),
                intensity=min(88, int(46 + 16 * strength)),
                cause=f"梦境余韵：{_single_line(label, 40)}",
                phase="dream_aftertaste",
            )
        return self._make_condition(
            kind="dream_aftertaste",
            title="梦后的朦胧残影",
            label="梦里的画面还没完全从脑子里退下去",
            mood="恍惚" if mood == "平稳" else mood,
            energy_delta=-max(1, int(round(2 * strength))),
            duration_hours=max(2, int(round(4 * strength))),
            intensity=min(82, int(42 + 16 * strength)),
            cause=f"梦境余韵：{_single_line(label, 40)}",
            phase="dream_aftertaste",
        )

    def _recent_diary_tags(self) -> set[str]:
        return recent_diary_tags(self)

    def _recent_diary_context(self, count: int = 3) -> str:
        return recent_diary_context(self, count)

    def _normalize_dream_fragment_item(self, raw: Any) -> dict[str, Any] | None:
        return normalize_dream_fragment_item(self, raw)

    def _dream_fragment_effective_weight(self, fragment: dict[str, Any], now_ts: float | None = None) -> float:
        return dream_fragment_effective_weight(self, fragment, now_ts=now_ts)

    def _normalize_dream_fragment_pool(self, fragments: Any, *, now_ts: float | None = None) -> list[dict[str, Any]]:
        return normalize_dream_fragment_pool(self, fragments, now_ts=now_ts)

    def _extract_weighted_dream_fragments(self, payload: Any) -> list[dict[str, Any]]:
        return extract_weighted_dream_fragments(self, payload)

    def _fallback_dream_fragments_for_diary(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return fallback_dream_fragments_for_diary(self, state)

    def _merge_dream_fragment_pool(self, new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return merge_dream_fragment_pool(self, new_items)

    def _weighted_unique_fragment_sample(
        self,
        fragments: list[dict[str, Any]],
        *,
        count: int,
    ) -> list[str]:
        return weighted_unique_fragment_sample(self, fragments, count=count)

    def _build_dream_memory_fragments(self, count: int = 8) -> list[str]:
        return build_dream_memory_fragments(self, count)

    def _dream_theme_specs(self) -> list[tuple[str, str]]:
        return dream_theme_specs(self)

    async def _generate_enhanced_dream_pick(
        self,
        weather: dict[str, Any] | None = None,
    ) -> tuple[str, str, int, int] | None:
        return await generate_enhanced_dream_pick(self, weather)

    async def _ensure_yesterday_screen_diary_context(self, force: bool = False) -> dict[str, Any]:
        today = _today_key()
        yesterday = date.today() - timedelta(days=1)
        source_date = _date_key(yesterday)
        cached = self.data.get("screen_diary_context", {})
        if (
            isinstance(cached, dict)
            and cached.get("date") == today
            and cached.get("source_date") == source_date
            and not force
        ):
            return cached
        screen_companion_available = False
        try:
            screen_companion_available = self._get_screen_companion_plugin() is not None
        except Exception:
            screen_companion_available = False
        if not runtime_persona_setting(self, "enable_yesterday_screen_diary_context", True) or not screen_companion_available:
            payload = {
                "date": today,
                "source_date": source_date,
            "source": "disabled" if not runtime_persona_setting(self, "enable_yesterday_screen_diary_context", True) else "screen_companion_unavailable",
                "summary": "",
                "items": [],
                "available": False,
            }
        else:
            payload = self._load_yesterday_screen_diary_context(yesterday)
        async with self._data_lock:
            self.data["screen_diary_context"] = payload
            self._save_data_sync(sections={"screen_diary_context"})
        return payload

    def _load_yesterday_screen_diary_context(self, target_date: date) -> dict[str, Any]:
        today = _today_key()
        source_date = _date_key(target_date)
        summary: dict[str, Any] = {}
        diary_text = ""
        source = "none"
        plugin = None
        try:
            plugin = self._get_screen_companion_plugin()
        except Exception:
            plugin = None
        if plugin is not None:
            loader = getattr(plugin, "_load_diary_structured_summary", None)
            if callable(loader):
                try:
                    raw_summary = loader(target_date)
                    if isinstance(raw_summary, dict):
                        summary = raw_summary
                        source = "screen_companion_api"
                except Exception as exc:
                    logger.debug("读取屏幕昨日结构化日记失败: %s", exc)
            if not summary:
                diary_storage = str(getattr(plugin, "diary_storage", "") or "").strip()
                summary = self._load_screen_diary_summary_file(target_date, diary_storage)
                if summary:
                    source = "screen_companion_file"
            diary_storage = str(getattr(plugin, "diary_storage", "") or "").strip()
            diary_text = self._load_screen_diary_markdown_file(target_date, diary_storage)
        if not summary:
            fallback_dirs = [
                str(Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_screen_companion" / "diary"),
                str(Path(__file__).resolve().parents[2] / "plugin_data" / "astrbot_plugin_screen_companion" / "diary"),
            ]
            for fallback_dir in fallback_dirs:
                summary = self._load_screen_diary_summary_file(target_date, fallback_dir)
                if summary:
                    source = "screen_companion_file"
                    if not diary_text:
                        diary_text = self._load_screen_diary_markdown_file(target_date, fallback_dir)
                    break
                if not diary_text:
                    diary_text = self._load_screen_diary_markdown_file(target_date, fallback_dir)
        items = self._screen_diary_items_from_summary(summary)
        if not items and diary_text:
            items = self._screen_diary_items_from_markdown(diary_text)
            if items and source == "none":
                source = "screen_companion_markdown"
        max_chars = max(200, _safe_int(runtime_persona_setting(self, "screen_diary_context_max_chars", 700), 700, 200, 1600))
        summary_text = self._format_screen_diary_context_items(source_date, items, max_chars=max_chars)
        return {
            "date": today,
            "source_date": source_date,
            "source": source,
            "summary": summary_text,
            "items": items[:8],
            "available": bool(summary_text),
        }

    def _load_screen_diary_summary_file(self, target_date: date, diary_dir: str = "") -> dict[str, Any]:
        if not diary_dir:
            return {}
        path = Path(diary_dir) / f"diary_{target_date.strftime('%Y%m%d')}.summary.json"
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.debug("读取屏幕日记摘要文件失败: %s", exc)
            return {}

    def _load_screen_diary_markdown_file(self, target_date: date, diary_dir: str = "") -> str:
        if not diary_dir:
            return ""
        path = Path(diary_dir) / f"diary_{target_date.strftime('%Y%m%d')}.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")[:4000]
        except Exception as exc:
            logger.debug("读取屏幕日记正文失败: %s", exc)
            return ""

    def _screen_diary_activity_label(self, text: Any) -> str:
        raw = str(text or "").lower()
        if not raw:
            return ""
        rules = (
            (("codex", "vscode", "visual studio", "pycharm", "idea", ".py", "插件", "编程", "代码", "终端", "powershell", "cmd"), "编程和插件调试"),
            (("qq", "微信", "wechat", "telegram", "discord", "会话", "聊天", "社交"), "社交消息"),
            (("chrome", "edge", "firefox", "浏览器", "网页", "搜索", "资料"), "查资料或网页浏览"),
            (("bilibili", "youtube", "视频", "番剧", "直播"), "视频或直播放松"),
            (("steam", "game", "游戏"), "游戏放松"),
            (("word", "excel", "wps", "文档", "表格", "写作"), "文档整理"),
            (("program manager", "桌面"), "桌面空档"),
        )
        for markers, label in rules:
            if any(marker in raw for marker in markers):
                return label
        return "电脑前活动"

    def _sanitize_screen_diary_text(self, text: Any, limit: int = 90) -> str:
        raw = _single_line(text, limit * 2)
        if not raw:
            return ""
        raw = re.sub(r"《[^》]{0,80}(?:》|$)", "相关窗口", raw)
        raw = re.sub(r"[\"“”'][^\"“”']{1,80}[\"“”']", "相关内容", raw)
        raw = re.sub(r"\bQQ\b|微信|WeChat|Telegram|Discord", "社交软件", raw, flags=re.IGNORECASE)
        raw = raw.replace("你在", "用户在")
        raw = raw.replace("我看到", "")
        raw = re.sub(r"\s+", " ", raw).strip(" ，。；;")
        return _single_line(raw, limit)

    def _screen_diary_items_from_summary(self, summary: dict[str, Any]) -> list[str]:
        if not isinstance(summary, dict) or not summary:
            return []
        items: list[str] = []
        main_windows = summary.get("main_windows") if isinstance(summary.get("main_windows"), list) else []
        labels: list[str] = []
        for item in main_windows[:4]:
            if not isinstance(item, dict):
                continue
            label = self._screen_diary_activity_label(item.get("window_title"))
            if label and label not in labels and label != "桌面空档":
                labels.append(label)
        if labels:
            items.append("主要节奏偏向：" + "、".join(labels[:3]))
        longest = summary.get("longest_task") if isinstance(summary.get("longest_task"), dict) else {}
        if longest:
            label = self._screen_diary_activity_label(
                f"{longest.get('window_title', '')} {longest.get('focus', '')}"
            )
            focus = self._sanitize_screen_diary_text(longest.get("focus"), 80)
            if label:
                items.append(f"最长专注大概落在{label}" + (f"，{focus}" if focus else ""))
        repeated = summary.get("repeated_focuses") if isinstance(summary.get("repeated_focuses"), list) else []
        repeated_labels: list[str] = []
        for item in repeated[:3]:
            if not isinstance(item, dict):
                continue
            label = self._screen_diary_activity_label(f"{item.get('window_title', '')} {item.get('note', '')}")
            if label and label not in repeated_labels and label != "桌面空档":
                repeated_labels.append(label)
        if repeated_labels:
            items.append("反复回到：" + "、".join(repeated_labels[:3]))
        suggestions = summary.get("suggestion_items") if isinstance(summary.get("suggestion_items"), list) else []
        for suggestion in suggestions[:2]:
            cleaned = self._sanitize_screen_diary_text(suggestion, 100)
            if cleaned and cleaned not in items:
                items.append("留给今天的背景：" + cleaned)
        return items[:6]

    def _screen_diary_items_from_markdown(self, diary_text: str) -> list[str]:
        raw = str(diary_text or "")
        if not raw.strip():
            return []
        lines = []
        in_overview = False
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("## 今日观察"):
                break
            if stripped.startswith("## 今日概览"):
                in_overview = True
                continue
            if in_overview and stripped.startswith("- "):
                cleaned = self._sanitize_screen_diary_text(stripped[2:], 100)
                label = self._screen_diary_activity_label(cleaned)
                if label and label != "电脑前活动":
                    cleaned = f"{label}：" + cleaned
                if cleaned:
                    lines.append(cleaned)
            if len(lines) >= 5:
                break
        if not lines:
            body = self._sanitize_screen_diary_text(raw, 220)
            if body:
                lines.append(body)
        return lines[:5]

    def _screen_diary_state_condition_spec(self) -> tuple[str, str, str, str, int, int, str] | None:
        payload = self.data.get("screen_diary_context", {})
        if not isinstance(payload, dict) or not payload.get("available"):
            return None
        text = str(payload.get("summary") or "")
        if not text:
            return None
        if any(token in text for token in ("编程", "调试", "查资料")):
            return (
                "user_yesterday_screen_diary",
                "昨日节奏残留",
                "昨天用户在电脑前专注处理代码或资料,今天对方可能还带着一点用脑后的疲惫",
                "留意,克制",
                -3,
                10,
                "来自昨日屏幕观察日记的脱敏节奏摘要",
            )
        if any(token in text for token in ("视频", "直播", "游戏")):
            return (
                "user_yesterday_screen_diary",
                "昨日节奏残留",
                "昨天用户有一段偏放松的电脑时间,今天可以把话题放得轻一点",
                "松弛",
                1,
                8,
                "来自昨日屏幕观察日记的脱敏节奏摘要",
            )
        if any(token in text for token in ("社交消息", "聊天")):
            return (
                "user_yesterday_screen_diary",
                "昨日节奏残留",
                "昨天用户处理过不少社交消息,今天靠近时更适合少一点压迫感",
                "轻一点",
                -1,
                8,
                "来自昨日屏幕观察日记的脱敏节奏摘要",
            )
        return None

    def _format_screen_diary_context_items(self, source_date: str, items: list[str], *, max_chars: int) -> str:
        if not items:
            return ""
        lines = [
            f"昨日屏幕观察日记（{source_date}，已脱敏，仅作背景）：",
        ]
        seen: set[str] = set()
        for item in items:
            cleaned = _single_line(item, 130)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            lines.append(f"- {cleaned}")
        lines.append("使用边界：只把它当作昨日生活节奏背景，影响今天的体力、作息和话题倾向；不要直接说“我昨天看到你”，不要复述窗口名、账号、聊天内容或具体隐私。")
        text = "\n".join(lines)
        return text[:max_chars]

    def _format_yesterday_screen_diary_context_for_prompt(self) -> str:
        if not runtime_persona_setting(self, "enable_yesterday_screen_diary_context", True):
            return "未启用。"
        payload = self.data.get("screen_diary_context", {})
        if not isinstance(payload, dict) or payload.get("date") != _today_key():
            return "暂无可用的昨日屏幕观察日记。"
        max_chars = max(200, _safe_int(runtime_persona_setting(self, "screen_diary_context_max_chars", 700), 700, 200, 1600))
        text = str(payload.get("summary") or "").strip()
        if len(text) > max_chars:
            text = text[:max_chars]
        return text or "暂无可用的昨日屏幕观察日记。"

    def _pick_diary_fragment(self) -> str:
        diaries = self.data.get("bot_diaries", [])
        if not isinstance(diaries, list) or not diaries:
            return ""
        diary = random.choice(diaries[-5:])
        if not isinstance(diary, dict):
            return ""
        candidates = [
            _single_line(diary.get("share_seed"), 100),
            _single_line(diary.get("summary"), 100),
        ]
        return next((item for item in candidates if item), "")
