# -*- coding: utf-8 -*-
"""CreativeAdvanceShareMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 300 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations
from .creative_shared import Any
from .creative_shared import _now_ts
from .creative_shared import _safe_float
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import asyncio
from .creative_shared import logger
from .creative_shared import random
from .creative_shared import runtime_persona_setting
from .creative_shared import story_legacy_operation
from .creative_shared import story_legacy_sync_operation



class CreativeAdvanceShareMixin:
    """CreativeAdvanceShareMixin（从 CreativeMixin 拆出）。"""


    @story_legacy_operation("creative.project.advance")
    async def _maybe_advance_creative_projects(self) -> None:
        if not runtime_persona_setting(self, "enable_creative_writing", False):
            return
        if self._creative_has_pending_proactive_plan():
            return
        if not self._bot_currently_idle_for_creative_writing():
            return
        await self._maybe_start_creative_project(idle_checked=True)
        projects = self._creative_projects()
        now = _now_ts()
        changed = False
        creative_record_payload: tuple[dict[str, Any], str, dict[str, Any] | None] | None = None
        for project in projects:
            if project.get("status") != "drafting":
                continue
            if now < _safe_float(project.get("next_advance_at"), 0):
                continue
            budget = int(self._creative_chars_per_session() * random.uniform(0.72, 1.18))
            budget = max(60, min(1200, self._creative_chunk_budget_for(project, budget)))
            remaining = _safe_int(project.get("target_chars"), 2400, 300, 5200) - _safe_int(project.get("current_chars"), 0, 0)
            if remaining <= 0:
                project["status"] = "finished"
                changed = True
                continue
            try:
                chunk = await self._generate_creative_chunk(project, min(budget, max(70, remaining)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay_minutes = self._defer_creative_project_advance(
                    project,
                    now=now,
                    reason=f"创作生成失败: {_single_line(exc, 140)}",
                )
                logger.warning(
                    "创作推进失败,已保留项目并退避: project=%s failures=%s delay=%sm error=%s",
                    _single_line(project.get("id"), 32),
                    _safe_int(project.get("advance_failure_count"), 1, 1),
                    delay_minutes,
                    _single_line(exc, 140),
                )
                changed = True
                break
            if not chunk:
                delay_minutes = self._defer_creative_project_advance(
                    project,
                    now=now,
                    reason="生成结果为空、重复或未通过质量复核",
                )
                logger.info(
                    "创作片段未通过质量门,本轮不写入资料柜: project=%s failures=%s delay=%sm",
                    _single_line(project.get("id"), 32),
                    _safe_int(project.get("advance_failure_count"), 1, 1),
                    delay_minutes,
                )
                changed = True
                break
            chunks = project.setdefault("draft_chunks", [])
            if not isinstance(chunks, list):
                chunks = []
                project["draft_chunks"] = chunks
            chunks.append({
                "at": now,
                "text": chunk,
                "chars": len(chunk),
            })
            del chunks[:-40]
            story_bible = self._get_or_create_story_bible(project)
            extract = await self._post_generation_extract(project, story_bible, chunk, len(chunks))
            pool = self._get_or_create_memory_pool(project)
            self._add_memory_entry(pool, str(project.get("id") or ""), "scene", chunk, story_bible.get("recent_keywords", []), importance=3)
            if isinstance(extract, dict):
                new_threads = extract.get("new_threads") if isinstance(extract.get("new_threads"), list) else []
                important_facts = extract.get("important_facts") if isinstance(extract.get("important_facts"), list) else []
                if new_threads:
                    self._add_memory_entry(pool, str(project.get("id") or ""), "theme", f"新线索: {_single_line(new_threads[0], 120)}", [str(t) for t in new_threads[:3]], importance=4)
                if important_facts:
                    self._add_memory_entry(pool, str(project.get("id") or ""), "fact", f"必须记住: {_single_line(important_facts[0], 160)}", [str(f) for f in important_facts[:3]], importance=5)
                nd = _single_line(extract.get("next_direction"), 120)
                if nd:
                    project["next_hint"] = nd
            project["current_chars"] = _safe_int(project.get("current_chars"), 0, 0) + len(chunk)
            project["last_advanced_at"] = now
            project["next_advance_at"] = now + self._creative_advance_gap_minutes(project, now) * 60
            project["advance_failure_count"] = 0
            project.pop("last_advance_failed_at", None)
            project.pop("last_advance_error", None)
            if project["current_chars"] >= _safe_int(project.get("target_chars"), 2400, 300, 5200):
                project["status"] = "finished"
            creative_record_payload = (dict(project), chunk, extract if isinstance(extract, dict) else None)
            changed = True
            break
        self.data["creative_projects"] = projects
        async with self._data_lock:
            if self._maybe_schedule_creative_share():
                changed = True
            if changed:
                self._save_data_sync(sections={"creative_projects"})
        if creative_record_payload is not None:
            recorder = getattr(self, "_memory_companion_record_creative_progress", None)
            if callable(recorder):
                project_snapshot, chunk_snapshot, extract_snapshot = creative_record_payload
                await recorder(project=project_snapshot, chunk=chunk_snapshot, extract=extract_snapshot)
        cover_project_id = self._creative_cover_candidate_id()
        if cover_project_id:
            await self._maybe_generate_creative_cover(cover_project_id)

    @story_legacy_sync_operation("creative.share.select")
    def _latest_creative_share_candidate(self) -> dict[str, Any] | None:
        projects = self._creative_projects()
        for project in reversed(projects):
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if not chunks:
                continue
            chunk = next((item for item in reversed(chunks) if isinstance(item, dict) and _single_line(item.get("text"), 260)), None)
            if not isinstance(chunk, dict):
                continue
            current_chars = _safe_int(project.get("current_chars"), 0, 0)
            target_chars = _safe_int(project.get("target_chars"), 2400, 300, 5200)
            story_bible = self._get_or_create_story_bible(project)
            snippet = _single_line(chunk.get("text"), 260)
            snippet_len = len(snippet)
            unresolved_threads = story_bible.get("unresolved_threads") if isinstance(story_bible.get("unresolved_threads"), list) else []
            important_facts = story_bible.get("important_facts") if isinstance(story_bible.get("important_facts"), list) else []
            chunk_count = len(chunks)
            completion_ratio = current_chars / max(1, target_chars)
            maturity_score = min(28.0, snippet_len / 6.5) + min(22.0, chunk_count * 5.0) + min(18.0, len(unresolved_threads) * 4.0) + min(16.0, len(important_facts) * 5.0) + min(16.0, completion_ratio * 24.0)
            disclosed = project.setdefault("disclosed_milestones", [])
            if not isinstance(disclosed, list):
                disclosed = []
                project["disclosed_milestones"] = disclosed
            milestone = ""
            disclosure_kind = "milestone"
            if project.get("status") == "finished" and "finished" not in disclosed:
                milestone = "finished"
            elif (
                current_chars >= max(180, int(target_chars * 0.14))
                and snippet_len >= 72 and maturity_score >= 36
                and "opening" not in disclosed
            ):
                milestone = "opening"
            elif (
                current_chars >= int(target_chars * 0.52)
                and snippet_len >= 88 and maturity_score >= 52
                and "midpoint" not in disclosed
            ):
                milestone = "midpoint"
            elif (
                current_chars >= max(520, int(target_chars * 0.3))
                and "impression_question" not in disclosed
                and chunk_count >= 3 and snippet_len >= 96 and maturity_score >= 58
                and random.random() < 0.28
            ):
                milestone = "impression_question"
                disclosure_kind = "ask_impression"
            if not milestone:
                continue
            return {
                "key": f"{project.get('id')}:{milestone}",
                "milestone": milestone,
                "disclosure_kind": disclosure_kind,
                "project_id": _single_line(project.get("id"), 20),
                "work_type": self._creative_work_type(project),
                "title": _single_line(project.get("title"), 40),
                "premise": _single_line(project.get("premise"), 140),
                "tone": _single_line(project.get("tone"), 40),
                "source": _single_line(project.get("source_text"), 140),
                "snippet": snippet,
                "current_chars": current_chars,
                "target_chars": target_chars,
                "chunk_count": chunk_count,
                "maturity_score": round(maturity_score, 2),
                "completion_ratio": round(completion_ratio, 4),
                "status": _single_line(project.get("status"), 24),
                "created_ts": _now_ts(),
            }
        return None

    @story_legacy_sync_operation("creative.share.disclose")
    def _mark_creative_milestone_disclosed(self, candidate: dict[str, Any]) -> None:
        project_id = _single_line(candidate.get("project_id"), 20)
        milestone = _single_line(candidate.get("milestone"), 40)
        if not project_id or not milestone:
            return
        for project in self._creative_projects():
            if _single_line(project.get("id"), 20) != project_id:
                continue
            disclosed = project.setdefault("disclosed_milestones", [])
            if not isinstance(disclosed, list):
                disclosed = []
                project["disclosed_milestones"] = disclosed
            if milestone not in disclosed:
                disclosed.append(milestone)
            break

    @story_legacy_sync_operation("creative.share.schedule")
    def _maybe_schedule_creative_share(self) -> bool:
        if not bool(runtime_persona_setting(self, "enable_creative_writing", False)):
            return False
        candidate = self._latest_creative_share_candidate()
        if not isinstance(candidate, dict):
            return False
        return self._schedule_creative_share_candidate(candidate)

    def _schedule_creative_share_candidate(
        self,
        candidate: dict[str, Any],
        *,
        mark_disclosed: bool = True,
    ) -> bool:
        """Schedule one validated candidate; the shelf owner marks disclosure."""

        if not isinstance(candidate, dict):
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        now = _now_ts()
        key = str(candidate.get("key") or "")
        completion_ratio = max(0.0, min(1.0, _safe_float(candidate.get("completion_ratio"), 0.0, 0.0)))
        maturity_score = _safe_float(candidate.get("maturity_score"), 0.0, 0.0)
        chunk_count = _safe_int(candidate.get("chunk_count"), 0, 0)
        disclosure_kind = _single_line(candidate.get("disclosure_kind"), 24) or "milestone"
        changed = False
        for user_id, user in users.items():
            if not isinstance(user, dict) or not self._is_target_private_user(str(user_id), user) or not user.get("enabled", True) or not user.get("umo"):
                continue
            if not self._friend_can_receive_proactive_reason(user, "creative_share", "message"):
                continue
            idle_seconds = now - _safe_float(user.get("last_seen"), 0)
            required_idle = max(runtime_persona_setting(self, "idle_minutes", 60), 75) * 60
            if disclosure_kind == "ask_impression":
                required_idle = max(required_idle, 120 * 60)
            if idle_seconds < required_idle:
                continue
            if str(user.get("last_creative_share_key") or "") == key:
                continue
            if now - _safe_float(user.get("last_creative_share_at"), 0) < 8 * 3600:
                continue
            relation_score = _safe_int(user.get("relationship_score"), 0, -40, 120)
            ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0, 20)
            rel_bonus = min(0.18, max(0.0, relation_score / 220.0))
            pressure_penalty = min(0.2, ignored_streak * 0.04)
            mat_bonus = min(0.22, max(0.0, (maturity_score - 40.0) / 120.0))
            comp_bonus = min(0.12, completion_ratio * 0.18)
            imp_penalty = 0.12 if disclosure_kind == "ask_impression" else 0.0
            share_p = max(
                0.08,
                min(
                    0.88,
                    float(runtime_persona_setting(self, "creative_share_probability", 0.28) or 0.0)
                    + rel_bonus
                    + mat_bonus
                    + comp_bonus
                    - pressure_penalty
                    - imp_penalty,
                ),
            )
            if chunk_count < 2 and disclosure_kind != "finished":
                continue
            if random.random() > share_p:
                continue
            if disclosure_kind == "finished":
                delay_minutes = random.randint(15, 45)
            elif disclosure_kind == "ask_impression":
                delay_minutes = random.randint(35, 120)
            else:
                delay_minutes = random.randint(22, 90)
            scheduled = now + delay_minutes * 60
            title = _single_line(candidate.get("title"), 40) or "刚开的创作项目"
            work_type = _single_line(candidate.get("work_type"), 30) or "作品"
            accepted = self._offer_proactive_candidate(
                str(user_id), user,
                {
                    "source": "creative_writing", "reason": "creative_share",
                    "action": "message", "scheduled_ts": scheduled, "topic": title,
                    "score": int(max(68, min(90, 66 + maturity_score * 0.18 + relation_score * 0.05 - ignored_streak * 1.5))),
                    "motive": f"刚把{work_type}《{title}》推进到一个比较成形的小节点,想自然地给 {user_id} 看一句",
                    "context_key": "creative_share_context", "context": dict(candidate),
                },
            )
            if not accepted:
                continue
            user["last_creative_share_key"] = key
            user["last_creative_share_at"] = now
            if mark_disclosed:
                self._mark_creative_milestone_disclosed(candidate)
            changed = True
        return changed

    def _creative_current_agenda_item(self) -> dict[str, Any] | None:
        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                item = getter()
            except Exception:
                return None
            return item if isinstance(item, dict) else None
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            item = legacy_getter(self.data.get("daily_plan", {})) if callable(legacy_getter) else None
        except Exception:
            item = None
        return item if isinstance(item, dict) else None
