# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPrivatePassivePromptPart01Mixin。

由 tools/split_mixin_domain.py 从 main_private_passive_prompt.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 478 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPrivatePassivePromptMixin）。
"""
from __future__ import annotations

from .main_private_passive_prompt_shared import logger
from .main_private_passive_prompt_shared import Any
from .main_private_passive_prompt_shared import AstrMessageEvent
from .main_private_passive_prompt_shared import CollectedPromptContext
from .main_private_passive_prompt_shared import PromptRenderMode
from .main_private_passive_prompt_shared import PromptSection
from .main_private_passive_prompt_shared import PromptSurface
from .main_private_passive_prompt_shared import ProviderRequest
from .main_private_passive_prompt_shared import _safe_float
from .main_private_passive_prompt_shared import _safe_int
from .main_private_passive_prompt_shared import _single_line
from .main_private_passive_prompt_shared import asyncio
from .main_private_passive_prompt_shared import prompt_section
from .main_private_passive_prompt_shared import re
from .main_private_passive_prompt_shared import render_prompt_sections
from .main_private_passive_prompt_shared import runtime_persona_setting



class PrivateCompanionPluginPrivatePassivePromptPart01Mixin:
    """PrivateCompanionPluginPrivatePassivePromptPart01Mixin（从 PrivateCompanionPluginPrivatePassivePromptMixin 拆出）。"""


    @staticmethod
    def _request_context_role(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("role") or "").strip().lower()
        return str(getattr(item, "role", "") or "").strip().lower()

    @staticmethod
    def _request_context_tool_calls(item: Any) -> list[Any]:
        raw = item.get("tool_calls") if isinstance(item, dict) else getattr(item, "tool_calls", None)
        return list(raw) if isinstance(raw, (list, tuple)) else []

    @staticmethod
    def _request_context_tool_call_id(item: Any) -> str:
        value = item.get("tool_call_id") if isinstance(item, dict) else getattr(item, "tool_call_id", None)
        return str(value or "").strip()

    @staticmethod
    def _request_context_declared_tool_call_id(item: Any) -> str:
        value = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        return str(value or "").strip()

    def _repair_incomplete_tool_context_groups(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """Drop broken tool-call groups atomically before strict providers see them."""
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return

        repaired: list[Any] = []
        removed_groups = 0
        removed_messages = 0
        index = 0
        while index < len(contexts):
            item = contexts[index]
            role = self._request_context_role(item)
            declared_calls = self._request_context_tool_calls(item) if role == "assistant" else []
            if declared_calls:
                declared_ids = [
                    self._request_context_declared_tool_call_id(call)
                    for call in declared_calls
                ]
                next_index = index + 1
                tool_messages: list[Any] = []
                while (
                    next_index < len(contexts)
                    and self._request_context_role(contexts[next_index]) == "tool"
                ):
                    tool_messages.append(contexts[next_index])
                    next_index += 1

                expected_ids = set(declared_ids)
                result_ids = {
                    self._request_context_tool_call_id(tool_message)
                    for tool_message in tool_messages
                    if self._request_context_tool_call_id(tool_message)
                }
                complete = (
                    bool(expected_ids)
                    and len(expected_ids) == len(declared_ids)
                    and expected_ids.issubset(result_ids)
                )
                if complete:
                    repaired.append(item)
                    kept_ids: set[str] = set()
                    for tool_message in tool_messages:
                        tool_call_id = self._request_context_tool_call_id(tool_message)
                        if tool_call_id in expected_ids and tool_call_id not in kept_ids:
                            repaired.append(tool_message)
                            kept_ids.add(tool_call_id)
                        else:
                            removed_messages += 1
                else:
                    removed_groups += 1
                    removed_messages += 1 + len(tool_messages)
                index = next_index
                continue

            if role == "tool":
                removed_messages += 1
            else:
                repaired.append(item)
            index += 1

        if removed_messages <= 0:
            return
        try:
            req.contexts = repaired
        except Exception:
            return
        logger.warning(
            "已修复不完整工具调用历史: session=%s groups=%s messages=%s contexts=%s->%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            removed_groups,
            removed_messages,
            len(contexts),
            len(repaired),
        )

    async def _collect_prompt_contexts_parallel(
        self,
        specs: list[dict[str, Any]],
    ) -> list[CollectedPromptContext]:
        tasks = [self._resolve_prompt_context_collector(spec) for spec in specs if isinstance(spec, dict)]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        collected: list[CollectedPromptContext] = []
        for result in results:
            if isinstance(result, CollectedPromptContext):
                collected.append(result)
            elif isinstance(result, Exception):
                logger.debug("请求上下文并行收集出现未捕获异常: %s", _single_line(result, 120))
        return collected

    def _add_collected_prompt_contexts(
        self,
        prompt_surface: PromptSurface,
        collected: list[CollectedPromptContext],
    ) -> None:
        for item in collected:
            for index, section in enumerate(item.sections):
                if not render_prompt_sections(
                    [section],
                    mode=PromptRenderMode.BODY_ONLY,
                ).strip():
                    continue
                prompt_surface.add(section, priority=item.priority + index)

    def _expression_profile_prompt_metadata(
        self,
        user: dict[str, Any],
        rule_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = user.get("expression_profile") if isinstance(user.get("expression_profile"), dict) else {}
        samples = profile.get("samples") if isinstance(profile.get("samples"), list) else []
        pending = profile.get("pending_samples") if isinstance(profile.get("pending_samples"), list) else []
        scene_profiles = profile.get("scene_profiles") if isinstance(profile.get("scene_profiles"), dict) else {}
        stable_scene_count = sum(
            1
            for item in scene_profiles.values()
            if isinstance(item, dict) and _safe_int(item.get("count"), 0, 0) >= 2
        )
        return {
            "来源": "表达学习样本",
            "置信度": min(1.0, round(len(samples) / 8, 2)) if samples else 0,
            "样本数": len(samples),
            "待审核": len(pending),
            "已学场景": stable_scene_count,
            "本轮命中": _single_line((rule_details or {}).get("label"), 32) or "无稳定规则",
            "规则证据": _safe_int((rule_details or {}).get("evidence_count"), 0, 0),
            "启用": bool(runtime_persona_setting(self, "enable_expression_learning", False)),
            "模式": _single_line(runtime_persona_setting(self, "expression_learning_mode", "balanced"), 20),
        }

    async def _collect_private_passive_prompt_contexts(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        inbound_text: str,
        current_user: dict[str, Any],
        is_private_chat: bool,
    ) -> list[CollectedPromptContext]:
        specs: list[dict[str, Any]] = []

        def add_spec(
            key: str,
            source: str,
            priority: int,
            func: Any,
            *,
            timeout: float = 0.8,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            specs.append(
                {
                    "key": key,
                    "source": source,
                    "priority": priority,
                    "func": func,
                    "timeout": timeout,
                    "metadata": metadata or {},
                }
            )

        current_user_id = ""
        if is_private_chat:
            try:
                current_user_id = _single_line(current_user.get("user_id") or event.get_sender_id(), 80)
            except Exception:
                current_user_id = _single_line(current_user.get("user_id"), 80)
        prompt_user = current_user
        current_umo = _single_line(getattr(event, "unified_msg_origin", ""), 220)
        if current_umo:
            prompt_user = dict(current_user)
            prompt_user["_game_current_umo"] = current_umo

        third_party_activity_question = self._user_activity_question_targets_someone_else(inbound_text)
        current_state_memory_needed = not third_party_activity_question and bool(
            self._user_asks_bot_current_state_or_activity(inbound_text)
            or re.search(
                r"(你|星缘|bot|机器人).{0,8}(在干嘛|在做什么|做什么|穿什么|穿的?什么|衣服|衣服颜色|什么颜色|吃了什么|吃的?什么|几点吃|什么时候吃|吃饭|进食|在哪里|在哪儿|当前位置|今天状态|现在状态)",
                inbound_text,
            )
            or re.search(
                r"(穿搭|自拍|衣服.{0,8}(颜色|什么色)|穿.{0,6}什么|今天.*衣服|今天.*颜色|刚才.*做|几点.*做了什么)",
                inbound_text,
            )
        )

        async def current_state_memory_context() -> PromptSection | None:
            composer = getattr(self, "_memory_companion_compose_feature_context", None)
            if not callable(composer):
                return None
            current_state_memory = await composer(
                kind="current_state_reply",
                query=(
                    f"当前状态问答：{inbound_text}；"
                    "今日穿搭、衣服颜色、当前日程、当前位置、刚才做了什么、进食时间、吃了什么、最近自拍、用户常问状态习惯"
                ),
                user=current_user,
                user_id=current_user_id,
                event=event,
                top_k=6,
                max_chars=950,
                timeout_seconds=1.6,
            )
            current_state_memory = str(current_state_memory or "").strip()
            if not current_state_memory:
                return None
            return prompt_section(
                key="memory.current_state",
                title="我会牢牢记住你 当前状态参考",
                source="memory_companion",
                content=(
                    f"{current_state_memory}\n"
                    "使用方式：只把它当作回答当前状态、穿搭、吃饭、日程连续性的辅助证据；"
                    "优先服从本轮状态注入和当前会话中明确发生的时间线。尤其是近期明确换装、换地点或动作变化，"
                    "高于每日穿搭、旧日程和旧记忆，不得被它们覆盖。不要说“我查到/记忆里”。"
                ),
                metadata={"范围": "当前私聊会话", "触发": "当前状态问答"},
            )

        if is_private_chat and current_state_memory_needed:
            add_spec(
                "memory.current_state",
                "memory_companion",
                54,
                current_state_memory_context,
                timeout=1.65,
                metadata={"范围": "当前私聊会话", "触发": "当前状态问答"},
            )

        add_spec(
            "creative.hidden",
            "creative",
            60,
            lambda: self._format_hidden_creative_context_for_reply_prompt_section(
                inbound_text,
                current_user,
            ),
        )
        add_spec(
            "photo.recent_share",
            "photo",
            61,
            lambda: self._format_recent_photo_share_snapshot_for_reply_prompt_section(
                current_user,
                inbound_text,
            ),
        )
        add_spec(
            "bookshelf.secret",
            "bookshelf",
            61,
            lambda: self._format_bookshelf_secret_prompt_section(inbound_text, current_user),
            timeout=1.2,
        )
        add_spec(
            "news.recent",
            "news",
            64,
            lambda: self._format_recent_news_context_prompt_section(inbound_text),
        )
        add_spec(
            "web_exploration.recent",
            "web_exploration",
            65,
            lambda: self._format_recent_web_exploration_context_prompt_section(inbound_text),
        )
        if is_private_chat:
            add_spec(
                "reality_touch.continuity",
                "reality_touch",
                56,
                lambda: self._format_reality_touch_continuity_context_prompt_section(
                    current_user
                ),
            )
            add_spec(
                "reality_touch.mobile_location",
                "reality_touch",
                55,
                lambda: self._format_mobile_user_location_context_prompt_section(
                    current_user
                ),
                metadata={"范围": "当前私聊会话", "来源": "用户主动授权的手机前台定位"},
            )
        if self._feature_enabled_or_temp_unlocked("enable_skill_growth_passive_injection"):
            add_spec("skill.growth", "skill", 66, self._format_skill_growth_prompt_section)
        else:
            add_spec(
                "skill.growth.match",
                "skill",
                66,
                lambda: self._format_skill_growth_for_user_text_prompt_section(inbound_text),
            )
        if not self._memory_companion_should_defer_prompt_section("self_timeline", event, req):
            add_spec(
                "self.timeline",
                "self_timeline",
                67,
                lambda: self._format_self_timeline_context_for_reply_section(
                    inbound_text,
                    current_user,
                    limit=8,
                ),
            )
        if is_private_chat:
            add_spec(
                "relationship.owner_exclusive",
                "relationship",
                18,
                lambda: self._format_owner_exclusive_relationship_prompt_section(
                    current_user,
                    stable_user_id=current_user_id,
                    channel_scope="private",
                ),
                metadata={"范围": "当前人格与精确私聊用户", "模式": "owner_exclusive"},
            )
        private_context_deferred = self._memory_companion_should_defer_prompt_section("private_context", event, req)
        if not private_context_deferred:
            add_spec(
                "private.context",
                "companion",
                70,
                lambda: self._format_private_chat_context_prompt_section(current_user),
            )
        if is_private_chat and not private_context_deferred:
            add_spec(
                "memory.private_recall",
                "memory_companion",
                73,
                lambda: self._memory_companion_compose_private_recall(
                    event=event,
                    user=current_user,
                    user_id=current_user_id,
                    text=inbound_text,
                ),
                timeout=min(1.4, max(0.3, _safe_float(getattr(self, "memory_companion_context_timeout_seconds", 1.2), 1.2, 0.2))),
                metadata={"范围": "当前私聊会话", "触发": "记忆线索"},
            )
        add_spec(
            "companion.planner",
            "companion",
            80,
            lambda: self._format_companion_planner_prompt_section(prompt_user),
        )
        if not self._memory_companion_should_defer_prompt_section("livingmemory_guidance", event, req):
            add_spec("livingmemory.guidance", "livingmemory", 90, lambda: self._format_livingmemory_guidance_sections(scope="private" if is_private_chat else "group"))
        add_spec("detail.injection", "daily_detail", 40, self._format_detail_injection_prompt_section)

        if is_private_chat:
            expression_user_id = self._expression_private_scope_id(current_user_id)
            expression_voice_selection = self._expression_voice_selection(
                scope="private",
                target_id=expression_user_id,
                inbound_text=inbound_text,
                context_owner=current_user,
            )
            expression_voice_section = expression_voice_selection.get("section")
            semantic_expression_rules = expression_voice_selection.get("rules")
            if isinstance(semantic_expression_rules, list) and semantic_expression_rules:
                try:
                    setattr(event, "private_companion_semantic_expression_rules", semantic_expression_rules)
                    setattr(
                        event,
                        "private_companion_semantic_expression_context",
                        dict(expression_voice_selection.get("context") or {}),
                    )
                except Exception:
                    pass
            if isinstance(expression_voice_section, PromptSection):
                add_spec(
                    "expression.voice",
                    "expression",
                    68,
                    lambda: expression_voice_section,
                    metadata={"范围": "全局抽象表达底色", "目标": expression_user_id},
                )

        async def timer_context() -> PromptSection | None:
            if not (self.enable_llm_timer_scheduling and is_private_chat):
                return None
            try:
                target_user_id = str(event.get_sender_id())
            except Exception:
                target_user_id = ""
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver) and target_user_id:
                target_user_id = resolver(event, target_user_id)
            if not target_user_id:
                return None
            async with self._data_lock:
                timer_user = dict(self._get_user(target_user_id))
                enabled = bool(timer_user.get("enabled"))
            return self._format_timer_scheduling_prompt_section(timer_user) if enabled else None

        add_spec("timer.scheduling", "timer", 95, timer_context, timeout=0.5)
        return await self._collect_prompt_contexts_parallel(specs)

    def _is_lightweight_private_passive_inbound(self, text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return False
        if len(cleaned) > 18:
            return False
        weather_query_detector = getattr(self, "_user_asks_current_weather", None)
        if callable(weather_query_detector) and weather_query_detector(cleaned):
            return False
        current_activity_detector = getattr(
            self,
            "_user_asks_bot_current_state_or_activity",
            None,
        )
        if callable(current_activity_detector) and current_activity_detector(cleaned):
            return False
        outfit_change_detector = getattr(self, "_detect_dialogue_outfit_change", None)
        if callable(outfit_change_detector):
            try:
                if outfit_change_detector(cleaned):
                    return False
            except Exception:
                pass
        heavy_tokens = (
            "图片", "看图", "照片", "语音", "引用", "转发", "聊天记录",
            "帮我", "怎么", "为什么", "是什么", "怎么办", "分析", "解释", "总结",
            "日程", "状态", "近况", "在干嘛", "干什么", "做什么", "忙什么",
            "资料柜", "夹层", "抽屉", "阅读", "读过", "看过", "素材", "资料", "漫画", "藏本",
            "创作", "作品", "写作", "写书", "写过书", "小说", "随笔", "散文", "剧本", "手稿", "草稿", "出版",
            "新闻", "说说", "空间", "发给", "转告", "@",
        )
        if any(token in cleaned for token in heavy_tokens):
            return False
        bookshelf_checker = getattr(self, "_user_asks_bookshelf_reading_memory", None)
        if callable(bookshelf_checker) and bookshelf_checker(cleaned):
            return False
        creative_checker = getattr(self, "_user_asks_recent_creative_activity", None)
        if callable(creative_checker) and creative_checker(cleaned):
            return False
        return True

    @staticmethod
    def _is_private_routine_check_invocation(text: str) -> bool:
        cleaned = _single_line(text, 80)
        if not cleaned or len(cleaned) > 28:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+", "", cleaned)
        markers = ("例行检查", "日常检查", "每日检查", "晚间检查", "夜间检查")
        prefixes = (
            "开始", "来", "继续", "进行", "该",
            "那", "那么", "那就", "嗯", "嗯那", "嗯那就", "好", "好吧", "好那就",
        )
        suffixes = ("啦", "咯", "了", "开始", "时间", "时间到", "一下")
        variants = set(markers)
        for marker in markers:
            variants.update(f"{prefix}{marker}" for prefix in prefixes)
            variants.update(f"{marker}{suffix}" for suffix in suffixes)
            variants.update(f"{prefix}{marker}{suffix}" for prefix in prefixes for suffix in suffixes)
        return compact in variants

    def _format_private_routine_check_boundary(
        self,
        text: str,
    ) -> str:
        section = self._format_private_routine_check_boundary_section(text)
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
