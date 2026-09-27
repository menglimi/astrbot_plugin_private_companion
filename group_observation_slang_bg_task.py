# -*- coding: utf-8 -*-
"""GroupObservationSlangBgTaskMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 380 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    prompt_text,
    render_prompt_document,
    render_prompt_sections,
)
from .group_observation_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from copy import deepcopy
from datetime import datetime
from typing import Any



class GroupObservationSlangBgTaskMixin:
    """GroupObservationSlangBgTaskMixin（从 GroupObservationMixin 拆出）。"""


    def _group_slang_prompt_document(
        self,
        terms: list[str],
        examples: list[str],
        *,
        web_evidence: str = "",
    ) -> PromptDocument:
        candidates = prompt_section(
            key="background.group_slang.candidates",
            title="候选词",
            source="group_observation",
            content=", ".join(terms),
        )
        group_examples = prompt_section(
            key="background.group_slang.examples",
            title="群聊样例",
            source="group_observation",
            content=(
                "\n".join(examples[-60:])
                + ("" if web_evidence else "\n\n")
            ),
        )
        evidence_sections: list[PromptSection] = [candidates, group_examples]
        if web_evidence:
            evidence_sections.append(
                prompt_section(
                    key="background.group_slang.web_evidence",
                    title="联网参考",
                    source="group_observation",
                    content=(
                        "下面是可选外部搜索摘要。它只能作为辅助证据,不能覆盖群聊样例；只有外部解释与本群样例能对应时才可采纳。"
                        "如果外部结果像百科、广告、无关网页、同词异义或无法匹配本群用法,请忽略。\n"
                        f"{web_evidence}\n"
                    ),
                )
            )
        root = prompt_section(
            key="background.group_slang",
            title="群聊黑话解释任务",
            source="group_observation",
            content=prompt_text(
                """请根据群聊样例,给这些群内常见词/梗做很短的语义解释。这是一个“黑话解释”专门任务。
只解释能从样例明确看出来的含义；证据不足、只是普通词、只是人名/群名片、只是口头语、含义不稳定时,直接不要输出这个词。
如果提供了联网参考,还要判断外部解释与本群样例的匹配程度；外部解释不匹配本群用法时必须以群聊样例为准。
不要写“语境不明”“可能是”“不确定”等模糊解释；低置信度宁可省略。
不要输出解释过程。""",
                render_prompt_sections(
                    evidence_sections,
                    mode=PromptRenderMode.LABELED_BLOCK,
                ),
                """只输出 JSON,键为词,值为对象：
{
  "某词": {
    "meaning": "一句话含义,必须是从样例能看出的稳定含义",
    "usage": "什么时候用,不确定就不要输出该词",
    "type": "外号|事件代称|梗|口头禅|调侃|称赞|辱骂|其他",
    "confidence": 0.0到1.0的小数,
    "evidence": "最能说明含义的一条短样例",
    "web_match": 0.0到1.0的小数,没有联网参考或不匹配就填0,
    "web_evidence": "联网参考中最相关的一句,没有就空字符串"
  }
}

入库标准：只输出 confidence >= 0.65 的词。无法达到就省略。""",
                separator="\n\n",
            ),
        )
        return prompt_document(user=[root])

    async def _maybe_refresh_group_slang_meanings(self, group_id: str, group: dict[str, Any]) -> None:
        if not _persona_value(self, "enable_group_slang_meanings", False):
            return
        now = _now_ts()
        async with self._data_lock:
            group = deepcopy(self._get_group(group_id))
        slang_summary_minutes = _safe_float(_persona_value(self, "group_slang_summary_minutes", 360), 360, 0)
        if now - _safe_float(group.get("last_slang_summary_at"), 0) < slang_summary_minutes * 60:
            return
        if now < _safe_float(group.get("group_slang_retry_after"), 0):
            return
        slang = group.get("slang_terms")
        if not isinstance(slang, list):
            return
        if self._cleanup_group_slang_terms(group):
            slang = group.get("slang_terms")
            if not isinstance(slang, list):
                return
        slang = [item for item in slang if self._group_slang_term_is_promoted(group, item)]
        if len(slang) < 3:
            return
        recent = self._filtered_group_recent_messages(group)
        terms = [
            _single_line(item.get("term"), 20)
            for item in slang[:20]
            if isinstance(item, dict) and _single_line(item.get("term"), 20)
        ]
        examples = []
        for item in recent[-80:]:
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 100)
            if any(term and term in text for term in terms[:12]):
                examples.append(f"{_single_line(item.get('name'), 18) or '群友'}: {text}")
        if not examples:
            return
        acquired = await self._try_acquire_group_background_task(
            group_id,
            "group_slang",
            now,
            refresh_key="last_slang_summary_at",
            refresh_seconds=slang_summary_minutes * 60,
        )
        if not acquired:
            return
        web_evidence = await self._collect_group_slang_web_evidence(group_id, terms, examples)
        slang_document = self._group_slang_prompt_document(
            terms,
            examples,
            web_evidence=web_evidence,
        )
        prompt = render_prompt_document(
            slang_document,
            mode=PromptRenderMode.BODY_ONLY,
        )["user"]
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=560,
                provider_id=self._task_provider(
                    _persona_value(self, "group_slang_provider_id", ""),
                    _persona_value(self, "mai_style_provider_id", ""),
                ),
                task="group_slang",
            )
            payload = self._extract_json_payload(raw or "")
        except Exception as exc:
            await self._mark_group_background_retry(group_id, "group_slang", now, exc)
            return
        if not isinstance(payload, dict):
            await self._mark_group_background_retry(group_id, "group_slang", now, "invalid_json")
            return
        normalized: dict[str, dict[str, str]] = {}
        for term, value in payload.items():
            key = _single_line(term, 20)
            if not key:
                continue
            if isinstance(value, dict):
                meaning = _single_line(value.get("meaning"), 90)
                usage = _single_line(value.get("usage"), 90)
                slang_type = _single_line(value.get("type"), 24)
                evidence = _single_line(value.get("evidence"), 120)
                web_match = min(1.0, _safe_float(value.get("web_match"), 0.0, 0.0))
                web_hit = _single_line(value.get("web_evidence"), 140)
                confidence = min(1.0, _safe_float(value.get("confidence"), 0.0, 0.0))
            else:
                meaning = _single_line(value, 90)
                usage = ""
                slang_type = ""
                evidence = ""
                web_match = 0.0
                web_hit = ""
                confidence = 0.0
            if not meaning or confidence < 0.65 or self._is_uncertain_group_slang_meaning(meaning, usage):
                continue
            normalized[key] = {
                "meaning": meaning,
                "usage": usage,
                "type": slang_type,
                "confidence": f"{confidence:.2f}",
                "evidence": evidence,
                "web_match": f"{web_match:.2f}" if web_match > 0 else "",
                "web_evidence": web_hit,
                "source": "llm_slang",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        async with self._data_lock:
            current = self._get_group(group_id)
            removed_uncertain = self._prune_uncertain_group_slang_meanings(current)
            meanings = current.setdefault("slang_meanings", {})
            if not isinstance(meanings, dict):
                meanings = {}
                current["slang_meanings"] = meanings
            for term, payload in normalized.items():
                existing = meanings.get(term)
                if isinstance(existing, dict) and existing.get("source") in {"explicit_correction", "manual"}:
                    continue
                meanings[term] = payload
            removed_budget = self._enforce_group_slang_meanings_budget(current)
            current["last_slang_summary_at"] = now
            current["group_slang_retry_after"] = 0
            current["group_slang_last_error"] = ""
            current["group_slang_running_at"] = 0
            if removed_uncertain:
                logger.info("[PrivateCompanion] 已清理低置信度群黑话释义: group=%s removed=%s", group_id, removed_uncertain)
            if removed_budget:
                logger.info("[PrivateCompanion] 已按预算收缩群黑话释义: group=%s removed=%s", group_id, removed_budget)
            self._save_data_sync(sections={"groups"})

    async def _try_acquire_group_background_task(
        self,
        group_id: str,
        task: str,
        now: float,
        *,
        refresh_key: str,
        refresh_seconds: float,
    ) -> bool:
        retry_key = f"{task}_retry_after"
        running_key = f"{task}_running_at"
        async with self._data_lock:
            current = self._get_group(group_id)
            if now - _safe_float(current.get(refresh_key), 0) < max(0.0, float(refresh_seconds)):
                return False
            if now < _safe_float(current.get(retry_key), 0):
                return False
            running_at = _safe_float(current.get(running_key), 0)
            if running_at > 0 and now - running_at < 10 * 60:
                return False
            current[running_key] = now
            self._save_data_sync(sections={"groups"})
        return True

    async def _mark_group_background_retry(self, group_id: str, task: str, now: float, error: Any) -> None:
        retry_key = f"{task}_retry_after"
        error_key = f"{task}_last_error"
        running_key = f"{task}_running_at"
        error_text = _single_line(error, 180)
        if task == "group_slang" and error_text == "invalid_json":
            async with self._data_lock:
                current = self._get_group(group_id)
                current["last_slang_summary_at"] = now
                current[retry_key] = 0
                current[error_key] = ""
                current[running_key] = 0
                self._save_data_sync(sections={"groups"})
            logger.debug(
                "群黑话释义 JSON 解析失败,已跳过本轮刷新: group=%s",
                group_id,
            )
            return
        delay = 10 * 60
        if task == "group_episode":
            delay = min(max(10 * 60, _safe_int(_persona_value(self, "group_episode_refresh_minutes", 60), 60, 1) * 60), 30 * 60)
        elif task == "group_slang":
            delay = min(max(10 * 60, _safe_int(_persona_value(self, "group_slang_summary_minutes", 360), 360, 1) * 60), 30 * 60)
        async with self._data_lock:
            current = self._get_group(group_id)
            current[retry_key] = now + delay
            current[error_key] = error_text
            current[running_key] = 0
            self._save_data_sync(sections={"groups"})
        logger.warning(
            "群聊后台整理失败,已进入短冷却避免重复请求: group=%s task=%s retry=%ss error=%s",
            group_id,
            task,
            int(delay),
            _single_line(error, 120),
        )

    async def _collect_group_slang_web_evidence(self, group_id: str, terms: list[str], examples: list[str]) -> str:
        if not bool(_persona_value(self, "enable_group_slang_web_search", False)):
            return ""
        picker = getattr(self, "_pick_available_web_search_umo", None)
        searcher = getattr(self, "_run_astrbot_web_search", None)
        if not callable(picker) or not callable(searcher):
            return ""
        search_umo = picker()
        if not search_umo:
            return ""
        term_limit = max(1, min(12, _safe_int(_persona_value(self, "group_slang_web_search_terms", 4), 4, 1, 12)))
        result_limit = max(1, min(5, _safe_int(_persona_value(self, "group_slang_web_search_results", 2), 2, 1, 5)))
        picked_terms: list[str] = []
        for term in terms:
            clean = _single_line(term, 20)
            if not clean or clean in picked_terms:
                continue
            if len(clean) <= 1:
                continue
            picked_terms.append(clean)
            if len(picked_terms) >= term_limit:
                break
        if not picked_terms:
            return ""
        now = _now_ts()
        async with self._data_lock:
            current = self._get_group(group_id)
            web_state = current.setdefault("slang_web_search_state", {})
            if not isinstance(web_state, dict):
                web_state = {}
                current["slang_web_search_state"] = web_state
            per_term = web_state.setdefault("terms", {})
            if not isinstance(per_term, dict):
                per_term = {}
                web_state["terms"] = per_term
            cursor = _safe_int(web_state.get("cursor"), 0, 0)
            ordered_terms = picked_terms[cursor % len(picked_terms):] + picked_terms[:cursor % len(picked_terms)]
            selected_term = ""
            cached_evidence = ""
            for term in ordered_terms:
                item = per_term.get(term)
                if not isinstance(item, dict):
                    item = {}
                    per_term[term] = item
                evidence = str(item.get("evidence") or "").strip()
                if evidence and now - _safe_float(item.get("last_success_at"), 0.0, 0.0) < 7 * 24 * 3600:
                    cached_evidence = evidence
                    continue
                if now < _safe_float(item.get("retry_after"), 0.0, 0.0):
                    continue
                selected_term = term
                break
            if not selected_term and cached_evidence:
                return cached_evidence[:1800]
            if not selected_term:
                return ""
        lines: list[str] = []
        term = selected_term
        query = f"群聊环境下的网络用语“{term}”是什么意思？"
        try:
            results = await searcher(query, umo=search_umo, topic="general")
        except Exception as exc:
            results = []
            self._last_web_search_error = _single_line(exc, 240)
            logger.debug("群黑话联网参考搜索失败: group=%s term=%s err=%s", group_id, term, _single_line(exc, 120))
        error_text = _single_line(getattr(self, "_last_web_search_error", ""), 240)
        if error_text and not results:
            async with self._data_lock:
                current = self._get_group(group_id)
                web_state = current.setdefault("slang_web_search_state", {})
                if isinstance(web_state, dict):
                    per_term = web_state.setdefault("terms", {})
                    if isinstance(per_term, dict):
                        item = per_term.setdefault(term, {})
                        if isinstance(item, dict):
                            item["last_error"] = error_text
                            item["retry_after"] = now + 30 * 60
                    try:
                        web_state["cursor"] = (picked_terms.index(term) + 1) % len(picked_terms)
                    except ValueError:
                        web_state["cursor"] = 0
                    web_state["last_error"] = error_text
                    web_state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._save_data_sync(sections={"groups"})
            logger.info(
                "群黑话联网参考单词搜索失败并冷却: group=%s term=%s error=%s",
                group_id,
                term,
                error_text,
            )
            return ""
        hits = []
        for item in results[:result_limit]:
            if not isinstance(item, dict):
                continue
            title = _single_line(item.get("title"), 80)
            snippet = _single_line(item.get("snippet"), 180)
            if not title and not snippet:
                continue
            hits.append(f"- {title}: {snippet}".strip())
        if hits:
            lines.append(f"{term}（搜索：{query}）:\n" + "\n".join(hits))
        async with self._data_lock:
            current = self._get_group(group_id)
            web_state = current.setdefault("slang_web_search_state", {})
            if isinstance(web_state, dict):
                per_term = web_state.setdefault("terms", {})
                if isinstance(per_term, dict):
                    item = per_term.setdefault(term, {})
                    if isinstance(item, dict):
                        item["last_search_at"] = now
                        item["retry_after"] = 0
                        item["last_error"] = ""
                        if lines:
                            item["last_success_at"] = now
                            item["evidence"] = "\n".join(lines)[:1800]
                try:
                    web_state["cursor"] = (picked_terms.index(term) + 1) % len(picked_terms)
                except ValueError:
                    web_state["cursor"] = 0
                web_state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_data_sync(sections={"groups"})
        if lines:
            logger.info("群黑话联网参考已收集: group=%s term=%s", group_id, term)
        return "\n".join(lines)[:1800]
