# -*- coding: utf-8 -*-
"""ProactiveMessageActionExecutionPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_action_execution.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 484 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageActionExecutionMixin）。
"""
from __future__ import annotations

from .proactive_message_action_execution_shared import _now_ts, logger
from .proactive_message_action_execution_shared import Any
from .proactive_message_action_execution_shared import PromptDocument
from .proactive_message_action_execution_shared import PromptRenderMode
from .proactive_message_action_execution_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_action_execution_shared import _proactive_prompt_part
from .proactive_message_action_execution_shared import _safe_float
from .proactive_message_action_execution_shared import _safe_int
from .proactive_message_action_execution_shared import _single_line
from .proactive_message_action_execution_shared import asyncio
from .proactive_message_action_execution_shared import importlib
from .proactive_message_action_execution_shared import math
from .proactive_message_action_execution_shared import prompt_document
from .proactive_message_action_execution_shared import prompt_section
from .proactive_message_action_execution_shared import random
from .proactive_message_action_execution_shared import render_prompt_document
from .proactive_message_action_execution_shared import render_prompt_sections
from .proactive_message_action_execution_shared import runtime_persona_setting
from .proactive_message_action_execution_shared import sys



class ProactiveMessageActionExecutionPart02Mixin:
    """ProactiveMessageActionExecutionPart02Mixin（从 ProactiveMessageActionExecutionMixin 拆出）。"""


    @staticmethod
    def _screen_peek_history_text(name: str) -> str:
        section = prompt_section(
            key="background.screen_peek.history",
            title="主动屏幕观察历史问题",
            source="proactive_message",
            content=f"主动陪伴想轻轻看一眼 {name} 现在在忙什么。",
        )
        return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)

    async def _classify_goodnight_screen_activity(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        name: str,
    ) -> str:
        plugin = self._get_screen_companion_plugin()
        if plugin is None or not callable(getattr(plugin, "_invoke_screen_skill", None)):
            return "uncertain"
        async with self._data_lock:
            current = self._get_user(user_id)
            self._note_screen_peek_attempt(user_id, reason="goodnight_screen_check", count_daily=True)
            self._save_data_sync(sections={"users"})

        event = None
        target = _single_line(user.get("umo"), 240)
        if target and hasattr(plugin, "_create_virtual_event"):
            try:
                event = plugin._create_virtual_event(target)
            except Exception as exc:
                logger.debug("创建晚安识屏虚拟事件失败: %s", _single_line(exc, 160))
        prompt = render_prompt_document(
            self._goodnight_screen_check_prompt_document()
        )["user"]
        try:
            result = await plugin._invoke_screen_skill(
                event,
                request_prompt=prompt,
                history_user_text=self._goodnight_screen_check_history_text(name),
                task_id="private_companion_goodnight_screen_check",
            )
        except Exception as exc:
            context = f"goodnight_screen_check：失败,{_single_line(exc, 240)}"
            logger.warning("晚安识屏判断失败: %s", _single_line(exc, 180))
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return "uncertain"
        if self._is_screen_peek_provider_failure(str(result or "")):
            self._note_screen_peek_failure(user, _single_line(result, 180))
            return "uncertain"
        parser = getattr(self, "_parse_json_object", None)
        parsed = parser(result) if callable(parser) else None
        if not isinstance(parsed, dict) and isinstance(result, dict):
            parsed = result
        state = _single_line(parsed.get("state"), 24).lower() if isinstance(parsed, dict) else ""
        return state if state in {"active", "inactive", "uncertain"} else "uncertain"

    async def _maybe_process_goodnight_screen_checks(self) -> None:
        now = _now_ts()
        claimed: list[tuple[str, float, str]] = []
        changed = False
        async with self._data_lock:
            users = self.data.get("users")
            if not isinstance(users, dict):
                return
            for raw_user_id, user in users.items():
                if not isinstance(user, dict):
                    continue
                due_at = _safe_float(user.get("goodnight_screen_check_due_at"), 0)
                if due_at <= 0 or due_at > now:
                    continue
                user_id = _single_line(user.get("user_id") or raw_user_id, 128)
                episode_at = _safe_float(user.get("goodnight_screen_check_episode_at"), 0)
                episode_key = _single_line(user.get("goodnight_screen_check_episode_key"), 180)
                user["goodnight_screen_check_due_at"] = 0
                user["goodnight_screen_check_checked_at"] = now
                user["goodnight_screen_check_checked_episode_key"] = episode_key
                user["goodnight_screen_check_state"] = "claimed"
                changed = True
                if user_id and episode_at > 0:
                    claimed.append((user_id, episode_at, episode_key))
            if changed:
                self._save_data_sync(sections={"users"})

        for user_id, episode_at, episode_key in claimed:
            async with self._data_lock:
                user = self._get_user(user_id)
                block_reason = self._goodnight_screen_check_block_reason(
                    user_id,
                    user,
                    episode_at=episode_at,
                    now=_now_ts(),
                    require_screen=True,
                )
                if block_reason:
                    user["goodnight_screen_check_state"] = block_reason
                    self._save_data_sync(sections={"users"})
                    continue
                name = _single_line(user.get("nickname"), 40) or user_id

            state = await self._classify_goodnight_screen_activity(user_id, user, name=name)
            async with self._data_lock:
                current = self._get_user(user_id)
                current["goodnight_screen_check_state"] = state
                current["goodnight_screen_check_result_at"] = _now_ts()
                self._save_data_sync(sections={"users"})
            if state != "active":
                continue

            async with self._data_lock:
                current = self._get_user(user_id)
                block_reason = self._goodnight_screen_check_block_reason(
                    user_id,
                    current,
                    episode_at=episode_at,
                    now=_now_ts(),
                    require_screen=False,
                )
                if block_reason:
                    current["goodnight_screen_check_state"] = block_reason
                    self._save_data_sync(sections={"users"})
                    continue
                current["proactive_sending"] = True
                current["proactive_sending_started_at"] = _now_ts()
                user = current
                name = _single_line(current.get("nickname"), 40) or user_id
                umo = _single_line(current.get("umo"), 240)
                self._save_data_sync(sections={"users"})

            motive = "互道晚安后仍有明确活动迹象，轻声提醒一次早点休息，不要求回复"
            safe_context = "内部状态判断：晚安后仍有明确活动迹象；没有提供任何屏幕内容或应用信息"
            try:
                text = await self._generate_proactive_message_with_llm(
                    user,
                    name,
                    "goodnight_screen_check",
                    action_context=safe_context,
                    action="message",
                    motive=motive,
                )
                if not text:
                    continue
                review = await self._review_proactive_message_send_decision(
                    user,
                    text,
                    reason="goodnight_screen_check",
                    action="message",
                    motive=motive,
                    topic="早点休息",
                    action_summary=safe_context,
                )
                decision = _single_line(review.get("decision"), 20).lower()
                if decision in {"drop", "defer"}:
                    continue
                if decision == "rewrite" and _single_line(review.get("text"), 500):
                    text = _single_line(review.get("text"), 500)
                outcome = await self._send_proactive_message_chain(umo, text)
                if not bool(getattr(outcome, "delivered", False)):
                    continue
                delivered_text = str(
                    getattr(outcome, "delivered_text", "") or text
                ).strip()
                delivery_umo = str(
                    getattr(outcome, "delivery_umo", "") or umo
                ).strip()
                assistant_archive_text = self._delivered_assistant_text_from_chain(
                    list(getattr(outcome, "delivered_chain", ()) or ()),
                    fallback_text=delivered_text,
                )
                if getattr(self, "context", None) is not None:
                    await self._archive_proactive_message_to_conversation(
                        user=user,
                        umo=delivery_umo,
                        user_prompt=self._build_proactive_archive_user_prompt(
                            reason="goodnight_screen_check",
                            action="message",
                            motive=motive,
                            action_summary=safe_context,
                        ),
                        assistant_response=assistant_archive_text,
                    )
                await self._record_final_assistant_in_livingmemory(
                    umo=delivery_umo,
                    assistant_response=assistant_archive_text,
                    delivery_id=f"goodnight:{user_id}:{_now_ts():.6f}",
                )
                memory_companion_recorder = getattr(
                    self,
                    "_memory_companion_record_proactive_message",
                    None,
                )
                if callable(memory_companion_recorder):
                    await memory_companion_recorder(
                        user=user,
                        user_id=user_id,
                        text=delivered_text,
                        umo=delivery_umo,
                        reason="goodnight_screen_check",
                        action="message",
                        motive=motive,
                        action_summary=safe_context,
                    )
                sent_at = _now_ts()
                visible = self._visible_text_without_tts_reading(delivered_text, limit=500)
                async with self._data_lock:
                    current = self._get_user(user_id)
                    self._reset_daily_counter_if_needed(current)
                    current["last_sent"] = sent_at
                    current["last_proactive_sent_at"] = sent_at
                    current["last_proactive_message"] = _single_line(visible, 500)
                    current["last_companion_message"] = _single_line(visible, 500)
                    current["last_companion_message_at"] = sent_at
                    current["last_proactive_reason"] = "goodnight_screen_check"
                    current["last_proactive_action"] = "message"
                    current["last_proactive_motive"] = motive
                    current["last_proactive_delivery_umo"] = delivery_umo
                    current["last_proactive_delivery_inbound_count"] = _safe_int(current.get("inbound_count"), 0)
                    current["goodnight_screen_check_reminded_at"] = sent_at
                    current["goodnight_screen_check_state"] = "reminded"
                    current["goodnight_screen_check_reminded_episode_key"] = episode_key
                    current["sent_today"] = _safe_int(current.get("sent_today"), 0) + 1
                    current["proactive_sent_count"] = _safe_int(current.get("proactive_sent_count"), 0) + 1
                    self._save_data_sync(sections={"users"})
            finally:
                async with self._data_lock:
                    current = self._get_user(user_id)
                    current["proactive_sending"] = False
                    current["proactive_sending_started_at"] = 0
                    self._save_data_sync(sections={"users"})

    @staticmethod
    def _screen_peek_prompt_document(reason: str) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.screen_peek",
                    title="主动屏幕观察",
                    source="proactive_message",
                    template=(
                        "这是一次用户已授权的主动陪伴行为。请只做视觉观察,"
                        "用很短的话描述用户电脑当前大概在看什么、做什么、是不是像在忙。"
                        "不要直接对用户说话,不要安慰、提醒、关心、陪伴,不要输出隐私细节、账号、完整文本、聊天内容。"
                        "只留一个内部观察印象。主动原因：{reason}"
                    ),
                    variables={"reason": reason},
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "screen_peek"},
        )

    async def _run_screen_peek_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        quota_exempt: bool = False,
    ) -> str:
        if not runtime_persona_setting(self, "enable_screen_glance_action", False):
            return "screen_peek：未授权,跳过"
        plugin = self._get_screen_companion_plugin()
        if plugin is None:
            return "screen_peek：屏幕插件不可用"
        target = str(user.get("umo") or "").strip()
        if not self._screen_glance_available(user, ignore_daily_limit=quota_exempt):
            return "screen_peek：今日额度或冷却未满足,跳过"
        async with self._data_lock:
            self._note_screen_peek_attempt(
                str(user.get("user_id") or user.get("umo") or name),
                reason=reason,
                count_daily=not quota_exempt,
            )
            self._save_data_sync(sections={"users"})
        event = None
        if target and hasattr(plugin, "_create_virtual_event"):
            try:
                event = plugin._create_virtual_event(target)
            except Exception as e:
                logger.debug(f"创建屏幕虚拟事件失败: {e}")
        prompt = render_prompt_document(
            self._screen_peek_prompt_document(reason)
        )["user"]
        try:
            result = await plugin._invoke_screen_skill(
                event,
                request_prompt=prompt,
                history_user_text=self._screen_peek_history_text(name),
                task_id="private_companion_screen_peek",
            )
            context = "screen_peek：\n" + (_single_line(result, 300) if result else "没有得到屏幕观察结果")
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return context
        except Exception as e:
            error_text = _single_line(e, 240)
            logger.warning(f"screen_peek 主动行为失败: {error_text}")
            context = f"screen_peek：失败,{error_text}"
            if self._is_screen_peek_provider_failure(context):
                self._note_screen_peek_failure(user, context)
            return context

    def _get_screen_companion_plugin(self) -> Any:
        # During a hot reload the registry may already contain the current
        # ScreenCompanion instance while the module singleton still points to
        # the previous one. Prefer the instance AstrBot dispatches, then keep
        # the module lookup as a compatibility fallback for older hosts.
        context = getattr(self, "context", None)
        get_one = getattr(context, "get_registered_star", None)
        if callable(get_one):
            try:
                metadata = get_one("astrbot_plugin_screen_companion")
            except Exception:
                metadata = None
            if metadata is not None and bool(getattr(metadata, "activated", True)):
                instance = getattr(metadata, "star_cls", None)
                if instance is not None and callable(getattr(instance, "_invoke_screen_skill", None)):
                    return instance
        for module_name in ("astrbot_plugin_screen_companion.main", "data.plugins.astrbot_plugin_screen_companion.main"):
            try:
                module = importlib.import_module(module_name)
                plugin = getattr(module, "_screen_companion_tool_plugin", None)
                if plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None)):
                    return plugin
            except Exception:
                continue
        for module in list(sys.modules.values()):
            try:
                plugin = getattr(module, "_screen_companion_tool_plugin", None)
                if plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None)):
                    return plugin
            except Exception:
                continue
        return None

    def _poke_action_cooldown_remaining(self, user: dict[str, Any] | None, *, now: float | None = None) -> float:
        if not isinstance(user, dict):
            return 0.0
        current_ts = float(now if now is not None else _now_ts())
        inflight_until = _safe_float(user.get("poke_action_inflight_until"), 0.0)
        if inflight_until > current_ts:
            return inflight_until - current_ts
        cooldown_seconds = max(
            0,
            _safe_int(
                runtime_persona_setting(self, "poke_action_cooldown_minutes", 30),
                30,
                0,
                1440,
            ),
        ) * 60
        last_at = _safe_float(user.get("last_poke_action_at"), 0.0)
        return max(0.0, last_at + cooldown_seconds - current_ts) if cooldown_seconds > 0 and last_at > 0 else 0.0

    async def _send_single_poke(self, client: Any, *, user_id: str, group_id: str) -> None:
        if group_id and callable(getattr(client, "group_poke", None)):
            await client.group_poke(group_id=int(group_id), user_id=int(user_id))
            return
        if not group_id and callable(getattr(client, "friend_poke", None)):
            await client.friend_poke(user_id=int(user_id))
            return
        try:
            from data.plugins.astrbot_plugin_pokepro.core.send_poke import PokeSender
        except Exception:
            from astrbot_plugin_pokepro.core.send_poke import PokeSender
        await PokeSender.poke_func(client=client, user_id=user_id, group_id=group_id or None)

    async def _run_poke_action(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        explicit_count: int | None = None,
    ) -> str:
        if not runtime_persona_setting(self, "enable_poke_action", False):
            return "poke：未启用"
        user_umo = str(user.get("umo") or "")
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("poke", umo=user_umo):
            return "poke：当前平台不支持戳一戳，已改用普通文字"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return "poke：未找到可用的 QQ 客户端"
        user_id = str(user.get("user_id") or "").strip()
        if not user_id.isdigit():
            return "poke：目标 QQ 号无效"
        group_id = self._extract_group_id_from_umo(str(user.get("umo") or ""))
        max_count = min(3, max(0, self._effective_user_poke_daily_limit(user)))
        if max_count <= 0:
            return "poke：当前用户未允许主动戳一戳"
        requested_count = int(explicit_count) if explicit_count is not None else self._choose_poke_repeat_count(user, reason)
        poke_count = max(1, min(max_count, requested_count))
        reserved = False
        try:
            async with self._data_lock:
                current = self._get_user(user_id)
                now = _now_ts()
                remaining = self._poke_action_cooldown_remaining(current, now=now)
                if remaining > 0:
                    return f"poke：冷却中，约 {max(1, math.ceil(remaining / 60))} 分钟后可再次执行"
                current["poke_action_inflight_until"] = now + max(30.0, poke_count * 3.0)
                current["poke_echo_suppress_until"] = now + max(30.0, poke_count * 3.0)
                self._save_data_sync(sections={"users"})
                reserved = True
            for index in range(poke_count):
                await self._send_single_poke(client, user_id=user_id, group_id=group_id)
                if index + 1 < poke_count:
                    await asyncio.sleep(random.uniform(0.35, 0.9))
            async with self._data_lock:
                current = self._get_user(user_id)
                current["last_poke_action_at"] = _now_ts()
                current["poke_action_inflight_until"] = 0
                self._save_data_sync(sections={"users"})
            if poke_count <= 1:
                return f"poke：已轻轻戳了 {name} 一下\n主动原因：{reason}"
            return f"poke：已轻轻连着戳了 {name} {poke_count} 下\n主动原因：{reason}"
        except Exception as e:
            if reserved:
                try:
                    async with self._data_lock:
                        current = self._get_user(user_id)
                        current["poke_action_inflight_until"] = 0
                        self._save_data_sync(sections={"users"})
                except Exception:
                    pass
            logger.warning(f"poke 主动行为失败: {e}")
            return f"poke：失败,{e}"

    def _choose_poke_repeat_count(self, user: dict[str, Any], reason: str) -> int:
        max_times = self._effective_user_poke_daily_limit(user)
        if max_times <= 0:
            return 0
        if max_times <= 1:
            return 1
        motive = _single_line(
            user.get("planned_proactive_motive") or user.get("last_proactive_motive"),
            120,
        )
        profile = self._persona_action_profile()
        weights: list[tuple[int, float]] = [(1, 1.0)]
        second_weight = 0.45
        third_weight = 0.12
        if profile.get("playful"):
            second_weight += 0.22
            third_weight += 0.1
        if profile.get("clingy"):
            second_weight += 0.12
            third_weight += 0.06
        if reason in {"quiet_care", "check_in"}:
            second_weight += 0.08
        if any(token in motive for token in ("轻轻叫你", "刷存在感", "碰你一下", "没忍住", "冒个头")):
            second_weight += 0.15
        if any(token in motive for token in ("偷偷看", "放心不下", "想起你", "不想吵你")):
            third_weight += 0.04
        weights.append((2, second_weight))
        if max_times >= 3:
            weights.append((3, third_weight))
        return int(self._weighted_choice([(str(count), weight) for count, weight in weights]))

    def _choose_pre_message_poke_count(
        self,
        user: dict[str, Any],
        reason: str,
        *,
        action: str = "message",
        motive: str = "",
    ) -> int:
        if "poke" in {part.strip() for part in str(action or "").split("+") if part.strip()}:
            return 0
        if (
            not self._poke_available()
            or self._effective_user_poke_daily_limit(user) <= 0
            or self._poke_action_cooldown_remaining(user) > 0
        ):
            return 0
        profile = self._persona_action_profile()
        probability = 0.12
        if reason in {"check_in", "quiet_care", "important_date_share"}:
            probability += 0.22
        if profile.get("playful"):
            probability += 0.14
        if profile.get("clingy"):
            probability += 0.08
        if action in {"voice", "photo_text"}:
            probability -= 0.02
        motive_text = str(motive or user.get("planned_proactive_motive") or "")
        if any(token in motive_text for token in ("轻轻叫你", "戳", "碰碰你", "确认一下", "放心不下", "叫你一声")):
            probability += 0.08
        probability = max(0.0, min(0.72, probability))
        if random.random() >= probability:
            return 0
        return self._choose_poke_repeat_count(user, reason)
