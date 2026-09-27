# -*- coding: utf-8 -*-
"""CreativeConfigStateMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 223 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations
from .creative_shared import Any
from .creative_shared import CREATIVE_FALLBACK_CHUNKS
from .creative_shared import CREATIVE_LEGACY_FALLBACK_CHUNKS
from .creative_shared import _now_ts
from .creative_shared import _safe_float
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import datetime
from .creative_shared import logger
from .creative_shared import random
from .creative_shared import re
from .creative_shared import runtime_persona_setting
from .creative_shared import story_legacy_context
from .creative_shared import story_legacy_sync_operation



class CreativeConfigStateMixin:
    """CreativeConfigStateMixin（从 CreativeMixin 拆出）。"""


    def _creative_projects(self) -> list[dict[str, Any]]:
        projects = self.data.get("creative_projects")
        valid_projects = (
            [item for item in projects if isinstance(item, dict)]
            if isinstance(projects, list)
            else []
        )
        needs_normalize = not isinstance(projects, list)
        for project in valid_projects:
            point_of_view = _single_line(project.get("point_of_view"), 40)
            needs_normalize = needs_normalize or "work_type" not in project
            needs_normalize = needs_normalize or not point_of_view
            needs_normalize = needs_normalize or bool(
                "第一人称" in point_of_view
                and not project.get("point_of_view_policy_version")
                and "书信" not in point_of_view
                and "日记" not in point_of_view
                and "手记" not in point_of_view
            )
        if not needs_normalize:
            return valid_projects
        with story_legacy_context("creative.projects.normalize"):
            if not isinstance(projects, list):
                projects = []
                self.data["creative_projects"] = projects
            valid_projects = [item for item in projects if isinstance(item, dict)]
            for project in valid_projects:
                project.setdefault("work_type", "短篇小说")
                point_of_view = _single_line(project.get("point_of_view"), 40)
                if not point_of_view:
                    project["point_of_view"] = "第三人称有限视角"
                    project.setdefault("point_of_view_policy_version", 2)
                    continue
                if (
                    "第一人称" in point_of_view
                    and not project.get("point_of_view_policy_version")
                    and "书信" not in point_of_view
                    and "日记" not in point_of_view
                    and "手记" not in point_of_view
                ):
                    project["point_of_view"] = "第三人称有限视角"
                    project["point_of_view_note"] = "legacy_first_person_rebalanced"
                    project["point_of_view_policy_version"] = 2
            return valid_projects

    def _creative_chars_per_session(self) -> int:
        style = str(runtime_persona_setting(self, "default_style", "温柔") or "")
        persona = "{} {} {}".format(
            runtime_persona_setting(self, "schedule_persona_prompt", ""),
            runtime_persona_setting(self, "default_style", "温柔"),
            runtime_persona_setting(self, "bot_name", "小星"),
        )
        budget = runtime_persona_setting(self, "creative_chars_per_session", 220)
        if any(token in persona for token in ("慢热", "寡言", "内敛", "病弱", "疲惫", "懒", "迟钝")):
            budget = int(budget * 0.72)
        elif any(token in persona for token in ("活泼", "话多", "元气", "急性子")) or style == "活泼":
            budget = int(budget * 1.18)
        elif style == "校园风":
            budget = int(budget * 0.88)
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        if energy < 40:
            budget = int(budget * 0.72)
        elif energy > 82:
            budget = int(budget * 1.12)
        return max(60, min(1200, budget))

    def _creative_advance_gap_minutes(
        self,
        project: dict[str, Any] | None = None,
        now: float | None = None,
        *,
        initial: bool = False,
    ) -> int:
        """拟人化写作节奏：按时段与人格决定下次推进间隔（分钟）。

        晚间是随手写两笔的黄金档，早晨在忙别的事；慢热型人格提笔更慢，
        偶尔"写得顺手"会在很短时间内再写一段，但不连续爆发。
        """
        now = _now_ts() if now is None else float(now)
        low, high = (35, 130) if initial else (95, 320)
        now_dt = datetime.fromtimestamp(now)
        if 20 <= now_dt.hour < 23 or (now_dt.hour == 23 and now_dt.minute <= 30):
            low, high = max(25, int(low * 0.7)), int(high * 0.62)
        elif 7 <= now_dt.hour < 10:
            low, high = int(low * 1.8), int(high * 1.3)
        persona = " ".join(
            str(runtime_persona_setting(self, name, "") or "")
            for name in ("schedule_persona_prompt", "default_style", "bot_name")
        )
        if any(token in persona for token in ("慢热", "寡言", "内敛", "病弱", "疲惫", "懒", "迟钝")):
            low, high = int(low * 1.35), int(high * 1.35)
        elif any(token in persona for token in ("活泼", "话多", "元气", "急性子")):
            low, high = int(low * 0.8), int(high * 0.8)
        project_dict = project if isinstance(project, dict) else {}
        last_burst = _safe_float(project_dict.get("last_creative_burst_at"), 0, 0)
        if (
            not initial
            and (not last_burst or now - last_burst >= 3 * 3600)
            and random.random() < 0.15
        ):
            project_dict["last_creative_burst_at"] = now
            return random.randint(25, 60)
        return random.randint(low, high)

    def _creative_chunk_budget_for(self, project: dict[str, Any] | None, base_budget: int) -> int:
        """按作品类型收紧单段字数：短诗小步快写，长叙事维持原有节奏。"""
        work_type = self._creative_work_type(project)
        budget = max(60, int(base_budget))
        if any(token in work_type for token in ("诗", "歌词", "短句")):
            return min(budget, 150)
        if any(token in work_type for token in ("随笔", "散文", "札记", "观察", "设定", "图鉴", "怪谈")):
            return min(budget, 400)
        return budget

    def _bot_currently_idle_for_creative_writing(self) -> bool:
        now_dt = datetime.now()
        if now_dt.hour < 7:
            return False
        current_item = self._creative_current_agenda_item()
        if self._is_sleepy_plan_item(current_item):
            return False
        activity = _single_line((current_item or {}).get("activity"), 100)
        mood = _single_line((current_item or {}).get("mood"), 40)
        seed = _single_line((current_item or {}).get("message_seed"), 100)
        state = self.data.get("daily_state", {})
        state_mood = _single_line(state.get("mood_bias") if isinstance(state, dict) else "", 30)
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        text = f"{activity} {mood} {seed} {state_mood}"
        busy_tokens = (
            "上课", "学习", "复习", "考试", "作业", "工作", "开会", "通勤",
            "忙", "赶", "处理", "训练", "任务", "外出", "出门", "睡",
        )
        if any(token in text for token in busy_tokens):
            return False
        idle_tokens = (
            "创作", "写字", "写作", "灵感", "读书", "阅读", "休息", "摸鱼",
            "发呆", "无聊", "闲", "空", "散步", "听歌", "整理", "安静",
            "下午也要加油", "缓一缓", "歇", "偷懒",
        )
        if any(token in text for token in idle_tokens):
            return random.random() < 0.55
        return 38 <= energy <= 82 and random.random() < 0.18

    def _creative_has_pending_proactive_plan(self) -> bool:
        now = _now_ts()
        users = self.data.get("users", {})
        if not isinstance(users, dict):
            return False
        for user in users.values():
            if not isinstance(user, dict):
                continue
            if bool(user.get("proactive_sending")):
                return True
            next_at = _safe_float(user.get("next_proactive_at"), 0)
            if 0 < next_at - now <= 20 * 60:
                return True
        return False

    @staticmethod
    def _creative_fallback_signature(text: Any) -> str:
        normalized = re.sub(r"\s+", "", str(text or "")).strip()
        return normalized.translate(str.maketrans({"，": ",", "；": ";", "：": ":"}))

    @classmethod
    def _is_legacy_creative_fallback_chunk(cls, text: Any) -> bool:
        signature = cls._creative_fallback_signature(text)
        if not signature:
            return False
        return signature in {
            cls._creative_fallback_signature(item)
            for item in (*CREATIVE_FALLBACK_CHUNKS, *CREATIVE_LEGACY_FALLBACK_CHUNKS)
        }

    @story_legacy_sync_operation("creative.startup.cleanup")
    def _cleanup_legacy_creative_fallback_chunks(self) -> bool:
        projects = self.data.get("creative_projects") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(projects, list):
            return False
        removed_chunks = 0
        cleaned_projects = 0
        removed_memories = 0
        for project in projects:
            if not isinstance(project, dict):
                continue
            chunks = project.get("draft_chunks")
            if not isinstance(chunks, list):
                continue
            kept_chunks = [
                chunk
                for chunk in chunks
                if not (
                    isinstance(chunk, dict)
                    and self._is_legacy_creative_fallback_chunk(chunk.get("text"))
                )
            ]
            removed_here = len(chunks) - len(kept_chunks)
            if removed_here <= 0:
                continue
            project["draft_chunks"] = kept_chunks
            project["current_chars"] = sum(
                len(str(chunk.get("text") or "").strip())
                for chunk in kept_chunks
                if isinstance(chunk, dict)
            )
            project["legacy_fallback_chunks_removed"] = (
                _safe_int(project.get("legacy_fallback_chunks_removed"), 0, 0) + removed_here
            )
            pool = project.get("creative_memory_pool")
            if isinstance(pool, list):
                kept_pool = [
                    item
                    for item in pool
                    if not (
                        isinstance(item, dict)
                        and self._is_legacy_creative_fallback_chunk(item.get("content"))
                    )
                ]
                removed_memories += len(pool) - len(kept_pool)
                project["creative_memory_pool"] = kept_pool
            removed_chunks += removed_here
            cleaned_projects += 1
        if removed_chunks:
            logger.info(
                "已清理资料柜旧版固定兜底片段: projects=%s chunks=%s memories=%s",
                cleaned_projects,
                removed_chunks,
                removed_memories,
            )
            return True
        return False
