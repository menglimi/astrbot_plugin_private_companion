# -*- coding: utf-8 -*-
"""GroupObservationInterjectEpisodeMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 540 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import hashlib
import os
import random
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    prompt_text,
    render_prompt_document,
)
from .group_observation_shared import _persona_value, build_group_episode_cache_prompts, logger
from .helpers import (
    _group_link_message_context,
    _normalize_outbound_punctuation_flow,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
    _today_key,
)
from astrbot.api.event import AstrMessageEvent
from copy import deepcopy
from datetime import datetime
from typing import Any
from .group_observation_shared import Plain



class GroupObservationInterjectEpisodeMixin:
    """GroupObservationInterjectEpisodeMixin（从 GroupObservationMixin 拆出）。"""


    def _update_group_repeat_follow_state(self, group: dict[str, Any], text: str, sender_id: str = "") -> dict[str, str]:
        if not _persona_value(self, "enable_group_repeat_follow", False):
            return {}
        cleaned = _single_line(text, 80)
        signature = self._group_repeat_signature(cleaned)
        if len(signature) < 1 or len(signature) > 30:
            group["repeat_follow_state"] = {}
            return {}
        now = _now_ts()
        sender_key = _single_line(sender_id, 64) or "unknown"
        count_distinct_users = bool(_persona_value(self, "group_repeat_count_distinct_users_only", False))
        state = group.get("repeat_follow_state")
        if not isinstance(state, dict):
            state = {}
        if signature and signature == str(state.get("signature") or "") and now - _safe_float(state.get("last_ts"), 0) <= 120:
            senders = state.get("senders") if isinstance(state.get("senders"), list) else []
            sender_is_new = sender_key not in senders
            if sender_is_new:
                senders.append(sender_key)
            state["senders"] = senders[-20:]
            state["count"] = _safe_int(state.get("count"), 1, 1) + 1
            state["distinct_count"] = len(set(state["senders"]))
            state["last_sender_id"] = sender_key
            state["last_ts"] = now
            state["text"] = cleaned
        else:
            state = {
                "signature": signature,
                "text": cleaned,
                "count": 1,
                "distinct_count": 1,
                "senders": [sender_key],
                "last_sender_id": sender_key,
                "first_ts": now,
                "last_ts": now,
                "acted": False,
                "follow_probability": max(0.0, _safe_float(_persona_value(self, "group_repeat_follow_probability", 0.0), 0.0, 0.0)),
                "interrupt_probability": max(0.0, _safe_float(_persona_value(self, "group_repeat_interrupt_probability", 0.0), 0.0, 0.0)),
            }
            sender_is_new = True
        group["repeat_follow_state"] = state
        count = _safe_int(state.get("distinct_count" if count_distinct_users else "count"), 1, 1)
        trigger_threshold = max(3, _safe_int(_persona_value(self, "group_repeat_trigger_threshold", 4), 4, 3))
        if count < trigger_threshold or bool(state.get("acted")) or bool(state.get("followed")):
            return {}
        today = _today_key()
        if group.get("interject_day") != today:
            group["interject_day"] = today
            group["interject_today"] = 0
        max_daily_getter = getattr(self, "_effective_group_interject_max_daily", None)
        max_daily = max_daily_getter() if callable(max_daily_getter) else _safe_int(_persona_value(self, "group_interject_max_daily", 0), 0, 0)
        if max_daily <= 0:
            return {}
        limit_unlimited = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        if (
            not (callable(limit_unlimited) and limit_unlimited(max_daily))
            and _safe_int(group.get("interject_today"), 0, 0) >= max_daily
        ):
            return {}
        follow_default = _safe_float(_persona_value(self, "group_repeat_follow_probability", 0.0), 0.0, 0.0)
        interrupt_default = _safe_float(_persona_value(self, "group_repeat_interrupt_probability", 0.0), 0.0, 0.0)
        follow_probability = min(0.85, _safe_float(state.get("follow_probability"), follow_default))
        interrupt_probability = min(0.85, _safe_float(state.get("interrupt_probability"), interrupt_default))
        total_probability = min(0.95, follow_probability + interrupt_probability)
        roll = random.random()
        if roll >= total_probability:
            if count_distinct_users and not sender_is_new:
                return {}
            step = max(0.0, _safe_float(_persona_value(self, "group_repeat_interrupt_probability_step", 0.0), 0.0, 0.0))
            state["follow_probability"] = min(0.85, follow_probability + step)
            state["interrupt_probability"] = min(0.85, interrupt_probability + step)
            return {}
        state["acted"] = True
        state["acted_ts"] = now
        action = "interrupt" if roll < interrupt_probability else "follow"
        if action == "interrupt":
            image_path = str(_persona_value(self, "group_repeat_interrupt_image_path", "") or "").strip()
            if image_path and not os.path.exists(image_path):
                image_path = ""
            text_reply = _single_line(_persona_value(self, "group_repeat_interrupt_text", ""), 80) or "禁止复读"
            return {"action": "interrupt", "text": "" if image_path else text_reply, "image_path": image_path}
        return {"action": "follow", "text": cleaned, "image_path": ""}

    def _group_interjection_prompt_document(
        self,
        group: dict[str, Any],
        text: str,
        *,
        memory_context: str = "",
    ) -> PromptDocument:
        group_context = prompt_section(
            key="background.group_interject.context",
            title="主动插话判断上下文",
            source="group_observation",
            content=self._format_group_context_for_prompt_body(group),
        )
        memory_reference = prompt_section(
            key="background.group_interject.memory_reference",
            title="我会牢牢记住你 群聊场合参考",
            source="group_observation",
            content=(
                f"{memory_context or '暂无可用长期参考。'}\n"
                "使用方式：只用于判断这个群、这些人和这个话题是否适合接话；不要在回复里提到记忆来源。"
            ),
        )
        voice_formatter = getattr(self, "_format_persona_voice_channel_prompt", None)
        persona_voice = (
            voice_formatter("proactive")
            if callable(voice_formatter)
            else "（未配置单独主动风格）"
        )
        persona_style = prompt_section(
            key="background.group_interject.persona_voice",
            title="人格标准化：群聊主动开口",
            source="group_observation",
            content=(
                f"{persona_voice}\n"
                "使用方式：只取“主动开口”的短句节奏和去 AI 味规则；群聊里要更轻,不要把私聊亲密度搬进群聊。"
            ),
        )
        trigger_message = prompt_section(
            key="background.group_interject.trigger_message",
            title="刚刚触发的消息",
            source="group_observation",
            content=_single_line(text, 180),
        )
        root = prompt_section(
            key="background.group_interject",
            title="群聊主动插话判断",
            source="group_observation",
            content=prompt_text(
                "你在一个群聊里,系统认为现在也许可以非常轻地接一句,但你必须先判断这句会不会显得硬插话。\n"
                "只输出 JSON,不要解释,不要 Markdown。",
                prompt_text(
                    prompt_heading_ref(group_context.title, newline=True),
                    group_context.content,
                ),
                prompt_text(
                    prompt_heading_ref(memory_reference.title, newline=True),
                    memory_reference.content,
                ),
                prompt_text(
                    prompt_heading_ref(persona_style.title, newline=True),
                    persona_style.content,
                ),
                prompt_text(
                    prompt_heading_ref(trigger_message.title, newline=True),
                    trigger_message.content,
                ),
                """要求：
- 如果这像群友之间的一对一、已经有人在自然接话、你这句没有新增价值,should_reply 必须为 false
- 链接、分享卡片以及围绕链接猜测内容的消息,should_reply 必须为 false
- 宁可不说,不要为了存在感插话
- should_reply 为 true 时,text 才能填写要发到群里的正文；1 句,最多 35 个中文字符
- should_reply 为 false 时,text 必须留空
- 像群友自然接话,不要像助手
- 只顺着当前话题轻轻补一句,不要开新话题,不要把自己变成中心
- 不要主持群聊,不要总结,不要 @ 人
- 不要提系统、观察、黑话学习、插件
- 如果不适合说话,should_reply 必须为 false

输出格式：
{"should_reply":false,"text":"","reason":"不超过12字"}""",
                separator="\n\n",
            ),
        )
        return prompt_document(user=[root])

    async def _maybe_group_interject(
        self,
        event: AstrMessageEvent,
        group: dict[str, Any],
        text: str,
        *,
        allow_interjection: bool = True,
        repeat_scene: dict[str, Any] | None = None,
    ) -> None:
        if bool(getattr(event, "private_companion_group_quoted_link_payload", False)):
            return
        _, has_link_payload = _group_link_message_context(text)
        if has_link_payload:
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        raw_wakeup = bool(getattr(event, "is_wake", False)) or bool(
            getattr(event, "is_at_or_wake_command", False)
        )
        scene = repeat_scene if isinstance(repeat_scene, dict) else getattr(
            event,
            "private_companion_group_scene",
            None,
        )
        has_structured_scene = isinstance(scene, dict) and bool(scene)
        talking_to = _single_line(scene.get("talking_to"), 80) if has_structured_scene else ""
        repeat_is_directed = (
            talking_to not in {"", "group"}
            if has_structured_scene
            else raw_wakeup
        )
        repeat_action: dict[str, str] = {}
        repeat_processed = bool(
            getattr(event, "_private_companion_group_repeat_processed", False)
        )
        if not repeat_processed:
            setattr(event, "_private_companion_group_repeat_processed", True)
            if not repeat_is_directed:
                repeat_action = self._update_group_repeat_follow_state(
                    group,
                    text,
                    sender_id=sender_id,
                )
        if repeat_action:
            repeat_reply = _single_line(repeat_action.get("text"), 80)
            image_path = str(repeat_action.get("image_path") or "")
            sent = await self._reply_with_optional_media(
                event,
                repeat_reply,
                image_path=image_path,
                quote_message_id="",
            )
            if sent is False:
                return
            now = _now_ts()
            self._record_group_bot_reply(
                group,
                text=repeat_reply or "[图片]",
                reply_to_id=sender_id,
                kind=(
                    "repeat_interrupt"
                    if repeat_action.get("action") == "interrupt"
                    else "repeat_follow"
                ),
                talking_to_bot=False,
                ts=now,
            )
            group["last_interject_at"] = now
            group["interject_today"] = _safe_int(group.get("interject_today"), 0, 0) + 1
            group["last_bot_interjection"] = {
                "ts": now,
                "text": repeat_reply,
                "reason": "群聊复读打断" if repeat_action.get("action") == "interrupt" else "群聊复读跟读",
                "has_image": bool(image_path),
                "topic_signature": self._group_topic_signature(text),
            }
            if raw_wakeup and has_structured_scene and talking_to in {"", "group"}:
                try:
                    event.stop_event()
                except Exception:
                    pass
            return
        if raw_wakeup:
            return
        if not allow_interjection:
            return
        allowed, reason = self._group_interjection_allowed(group, text)
        if not allowed:
            return
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="group_interjection",
                    query=(
                        f"群聊主动插话判断：群={group.get('group_id') or ''}；触发消息={_single_line(text, 180)}；"
                        "群聊最近谁在对话、谁不喜欢被cue、上次插话效果、关系边界、常聊话题、是否适合轻接一句"
                    ),
                    event=event,
                    top_k=5,
                    max_chars=900,
                    timeout_seconds=1.2,
                )
            except Exception as exc:
                logger.debug("群聊插话 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        interjection_document = self._group_interjection_prompt_document(
            group,
            text,
            memory_context=memory_context,
        )
        prompt = render_prompt_document(
            interjection_document,
            mode=PromptRenderMode.BODY_ONLY,
        )["user"]
        generated = await self._llm_call(
            prompt,
            max_tokens=140,
            provider_id=self._task_provider(
                _persona_value(self, "group_interject_provider_id", ""),
                _persona_value(self, "mai_style_provider_id", ""),
            ),
            task="group_interject",
        )
        should_reply, reply, skip_reason = self._parse_group_interjection_decision(generated)
        if not should_reply or not reply:
            if skip_reason:
                logger.debug(
                    "群聊主动插话模型决定不发言: group=%s reason=%s raw=%s",
                    group.get("group_id") or "",
                    _single_line(skip_reason, 80),
                    _single_line(generated, 120),
                )
            return
        reply = _normalize_outbound_punctuation_flow(reply)
        if self._response_review_flags(reply, {}):
            return
        quote_message_id = self._resolve_quote_message_id(
            event,
            scene_name="group_interjection",
            text_or_chain=reply,
        )
        if quote_message_id:
            await event.send(event.chain_result(self._with_optional_reply([Plain(reply)], quote_message_id, event=event)))
        else:
            await event.send(event.plain_result(reply))
        group["last_interject_at"] = _now_ts()
        self._record_group_bot_reply(
            group,
            text=reply,
            reply_to_id=sender_id,
            kind="interjection",
            talking_to_bot=False,
            ts=group["last_interject_at"],
        )
        group["interject_today"] = _safe_int(group.get("interject_today"), 0, 0) + 1
        group["last_bot_interjection"] = {
            "ts": group["last_interject_at"],
            "text": reply,
            "reason": reason,
            "topic_signature": self._group_topic_signature(text),
        }
        logger.info(
            "群聊主动插话已发送: group=%s reason=%s trigger=%s reply=%s",
            group.get("group_id") or "",
            _single_line(reason, 80),
            _single_line(text, 80),
            _single_line(reply, 80),
        )
        threads = group.get("topic_threads")
        if isinstance(threads, list):
            signature = self._group_topic_signature(text)
            for item in threads:
                if isinstance(item, dict) and self._topic_signature_similar(signature, str(item.get("signature") or "")):
                    item["bot_joined"] = True
                    item["bot_joined_ts"] = group["last_interject_at"]
                    break

    async def _try_reserve_group_expression_rule_batch(
        self,
        group_id: str,
        *,
        batch_key: str,
        candidate_count: int,
        now: float,
    ) -> bool:
        day = _today_key()
        limit = _safe_int(
            _persona_value(self, "expression_group_learning_daily_batch_limit", 6),
            6,
            1,
            50,
        )
        async with self._data_lock:
            current = self._get_group(group_id)
            if _single_line(current.get("last_expression_rule_attempt_day"), 20) == day:
                return False
            if _single_line(current.get("last_expression_rule_batch_key"), 40) == batch_key:
                return False
            runtime = self.data.setdefault("expression_learning_runtime", {})
            if not isinstance(runtime, dict):
                runtime = {}
                self.data["expression_learning_runtime"] = runtime
            by_day = runtime.setdefault("group_batches_by_day", {})
            if not isinstance(by_day, dict):
                by_day = {}
                runtime["group_batches_by_day"] = by_day
            used = _safe_int(by_day.get(day), 0, 0)
            if used >= limit:
                runtime["last_group_deferred_at"] = now
                runtime["last_group_defer_reason"] = "daily_batch_limit"
                return False
            by_day[day] = used + 1
            for old_day in sorted(by_day)[:-14]:
                by_day.pop(old_day, None)
            runtime["last_group_batch_at"] = now
            runtime["last_group_batch_id"] = _single_line(group_id, 80)
            current["last_expression_rule_attempt_day"] = day
            current["last_expression_rule_attempt_at"] = now
            current["last_expression_rule_batch_key"] = batch_key
            current["last_expression_rule_candidate_count"] = max(0, int(candidate_count))
            self._save_data_sync(sections={"groups", "expression_learning_runtime"})
        return True

    async def _maybe_refresh_group_episode(self, group_id: str, group: dict[str, Any]) -> None:
        if not _persona_value(self, "enable_group_episode_memory", False):
            return
        now = _now_ts()
        async with self._data_lock:
            group = deepcopy(self._get_group(group_id))
        episode_refresh_minutes = _safe_float(_persona_value(self, "group_episode_refresh_minutes", 60), 60, 0)
        if now - _safe_float(group.get("last_episode_refresh_at"), 0) < episode_refresh_minutes * 60:
            return
        if now < _safe_float(group.get("group_episode_retry_after"), 0):
            return
        recent = self._filtered_group_recent_messages(group)
        if len(recent) < 12:
            return
        lines = []
        expression_candidate_lines: list[str] = []
        expression_cursor = _safe_float(group.get("last_expression_rule_source_ts"), 0.0)
        expression_source_ts = expression_cursor
        for item in recent[-80:]:
            if not isinstance(item, dict):
                continue
            name = _single_line(item.get("name"), 20) or "群友"
            text = _single_line(item.get("text"), 100)
            if text:
                line = f"{name}: {text}"
                lines.append(line)
                message_ts = _safe_float(item.get("ts"), 0.0)
                if expression_cursor <= 0 or (message_ts > 0 and message_ts > expression_cursor):
                    expression_candidate_lines.append(line)
                    expression_source_ts = max(expression_source_ts, message_ts)
        if len(lines) < 8:
            return
        min_new_messages = _safe_int(
            _persona_value(self, "expression_group_learning_min_new_messages", 20),
            20,
            5,
            80,
        )
        wants_expression_rules = bool(
            _persona_value(self, "enable_expression_learning", False)
            and len(expression_candidate_lines) >= min_new_messages
            and self._expression_group_learning_source_enabled(group_id)
        )
        expression_source_text = chr(10).join(expression_candidate_lines)
        expression_batch_key = hashlib.sha1(expression_source_text.encode("utf-8")).hexdigest()[:20]
        acquired = await self._try_acquire_group_background_task(
            group_id,
            "group_episode",
            now,
            refresh_key="last_episode_refresh_at",
            refresh_seconds=episode_refresh_minutes * 60,
        )
        if not acquired:
            return
        learn_expression_rules = bool(
            wants_expression_rules
            and await self._try_reserve_group_expression_rule_batch(
                group_id,
                batch_key=expression_batch_key,
                candidate_count=len(expression_candidate_lines),
                now=now,
            )
        )
        existing_rule_reference = ""
        if learn_expression_rules:
            existing_rule_reference = self._expression_rule_generation_reference(
                group.get("expression_profile"),
                hint="\n".join(expression_candidate_lines),
            )
        system_prompt, prompt = build_group_episode_cache_prompts(
            lines,
            learn_expression_rules=learn_expression_rules,
            candidate_count=len(expression_candidate_lines),
            existing_rule_reference=existing_rule_reference,
        )
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=760 if learn_expression_rules else 420,
                provider_id=self._task_provider(
                    _persona_value(self, "group_episode_provider_id", ""),
                    _persona_value(self, "mai_style_provider_id", ""),
                ),
                task="group_episode",
                system_prompt=system_prompt,
            )
            if not str(raw or "").strip():
                await self._mark_group_background_retry(group_id, "group_episode", now, "llm_no_result")
                return
            payload = self._extract_json_payload(raw or "")
        except Exception as exc:
            await self._mark_group_background_retry(group_id, "group_episode", now, exc)
            return
        if not isinstance(payload, dict):
            await self._mark_group_background_retry(group_id, "group_episode", now, "invalid_json")
            return
        episode = {
            "date": _today_key(),
            "created_ts": now,
            "summary": _single_line(payload.get("summary"), 140),
            "main_topics": self._normalize_string_list(payload.get("main_topics"), limit=6, item_limit=50),
            "new_meme": _single_line(payload.get("new_meme"), 60),
            "active_people": self._normalize_string_list(payload.get("active_people"), limit=8, item_limit=30),
            "avoid_repeat": self._normalize_string_list(payload.get("avoid_repeat"), limit=6, item_limit=60),
        }
        expression_rules = self._normalize_expression_rule_candidates(
            self._expression_rule_payload_candidates(payload),
            source_kind="group",
            source_text=expression_source_text,
        ) if learn_expression_rules else []
        if not episode["summary"] and not expression_rules:
            await self._mark_group_background_retry(group_id, "group_episode", now, "empty_summary")
            return
        async with self._data_lock:
            current = self._get_group(group_id)
            episodes = current.setdefault("group_episodes", [])
            if not isinstance(episodes, list):
                episodes = []
                current["group_episodes"] = episodes
            if episode["summary"] and (
                not episodes
                or _single_line(episodes[-1].get("summary") if isinstance(episodes[-1], dict) else "", 140) != episode["summary"]
            ):
                episodes.append(episode)
            del episodes[:-_safe_int(_persona_value(self, "max_group_episodes", 40), 40, 1)]
            if expression_rules:
                expression_profile = current.setdefault("expression_profile", {})
                if not isinstance(expression_profile, dict):
                    expression_profile = {}
                    current["expression_profile"] = expression_profile
                self._merge_learned_expression_rules(
                    expression_profile,
                    expression_rules,
                    batch_key=expression_batch_key,
                    now=now,
                    pending=True,
                )
                expression_profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._refresh_expression_voice_profile()
            if learn_expression_rules:
                current["last_expression_rule_source_ts"] = expression_source_ts or now
                current["last_expression_rule_completed_at"] = now
            current["last_episode_refresh_at"] = now
            current["group_episode_retry_after"] = 0
            current["group_episode_last_error"] = ""
            current["group_episode_running_at"] = 0
            save_sections = {"groups"}
            if expression_rules:
                save_sections.add("expression_voice_profile")
            self._save_data_sync(sections=save_sections)
