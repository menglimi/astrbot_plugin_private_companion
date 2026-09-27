# -*- coding: utf-8 -*-
"""ProactiveMessageActionExecutionPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_action_execution.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 484 行）。
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
from .proactive_message_action_execution_shared import deepcopy
from .proactive_message_action_execution_shared import os
from .proactive_message_action_execution_shared import prompt_document
from .proactive_message_action_execution_shared import prompt_section
from .proactive_message_action_execution_shared import re
from .proactive_message_action_execution_shared import render_prompt_sections
from .proactive_message_action_execution_shared import runtime_persona_setting



class ProactiveMessageActionExecutionPart01Mixin:
    """ProactiveMessageActionExecutionPart01Mixin（从 ProactiveMessageActionExecutionMixin 拆出）。"""


    async def _execute_proactive_action(
        self,
        action: str,
        user: dict[str, Any],
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        normalized = str(action or "message").strip() or "message"
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        if not parts:
            parts = ["message"]
        contexts: list[str] = []
        extra_components: list[Any] = []
        summary_parts: list[str] = []
        effective_parts: list[str] = []
        for part in parts:
            payload = await self._execute_single_action(part, user, name, reason)
            contexts.append(str(payload.get("context") or "").strip())
            extra_components.extend(list(payload.get("extra_components") or []))
            summary = _single_line(payload.get("summary") or part, 60)
            if summary:
                summary_parts.append(summary)
            effective_action = _single_line(payload.get("effective_action") or part, 40)
            if effective_action:
                effective_parts.append(effective_action)
            if not bool(payload.get("success", True)):
                return {
                    "success": False,
                    "context": "\n".join(item for item in contexts if item),
                    "extra_components": [],
                    "summary": " + ".join(summary_parts) or normalized,
                    "effective_action": "+".join(effective_parts) or normalized,
                }
        return {
            "success": True,
            "context": "\n".join(item for item in contexts if item) or "message：只发送私聊文本",
            "extra_components": extra_components,
            "summary": " + ".join(summary_parts) or normalized,
            "effective_action": "+".join(effective_parts) or normalized,
        }

    async def _execute_single_action(
        self,
        action: str,
        user: dict[str, Any],
        name: str,
        reason: str,
    ) -> dict[str, Any]:
        fallback_action = self._fallback_action_for_unavailable(action, user)
        if fallback_action != action:
            logger.info(
                "主动行为依赖不可用,已回退: requested=%s fallback=%s user=%s",
                action,
                fallback_action,
                str(user.get("user_id") or ""),
            )
            if fallback_action == "message":
                return {
                    "success": True,
                    "context": "message：只发送普通私聊文本",
                    "extra_components": [],
                    "summary": "文字",
                    "effective_action": "message",
                }
            return await self._execute_single_action(fallback_action, user, name, reason)
        if action == "screen_peek":
            context = await self._run_screen_peek_action(
                user,
                name,
                reason,
                quota_exempt=bool(user.get("planned_proactive_quota_exempt")),
            )
            return {
                "success": not self._is_unusable_screen_peek_context(context),
                "context": context,
                "extra_components": [],
                "summary": "窥屏",
                "effective_action": "screen_peek",
            }
        if action == "photo_text":
            context = await self._run_photo_text_action(user, name, reason)
            image_ready = "真实图片" in context and "图片路径：" in context
            if not image_ready and reason == "birthday_celebration":
                return {
                    "success": True,
                    "context": "message：生日卡未生成，改为只发送生日祝福正文",
                    "extra_components": [],
                    "summary": "生日祝福文字",
                    "effective_action": "message",
                }
            return {
                "success": image_ready,
                "context": context,
                "extra_components": [],
                "summary": "发图",
                "effective_action": "photo_text",
            }
        if action == "poke":
            context = await self._run_poke_action(user, name, reason)
            return {
                "success": context.startswith("poke：已"),
                "context": context,
                "extra_components": [],
                "summary": "戳了你一下",
                "effective_action": "poke",
            }
        if "voice" in action and "photo_text" not in action:
            payload = await self._run_voice_action(user, name, reason)
            payload.setdefault("summary", "留了句语音")
            payload.setdefault("effective_action", "voice")
            return payload
        if action.startswith("external:"):
            return await self._execute_external_proactive_ability(action.split(":", 1)[1], user, name, reason)
        return {"success": True, "context": "message：只发送私聊文本", "extra_components": [], "summary": "文字", "effective_action": "message"}

    async def _execute_external_proactive_ability(
        self,
        ability_name: str,
        user: dict[str, Any],
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        user = user if isinstance(user, dict) else {}
        name = self._normalize_external_ability_name(ability_name)
        runtime = self._external_proactive_abilities.get(name)
        if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
            return {"success": False, "context": "external：外部主动能力未注册或不可用", "extra_components": [], "summary": "外部能力不可用", "effective_action": "message"}
        user_key = _single_line(
            user.get("user_id") or user.get("id") or user.get("umo"),
            180,
        ) or "global"
        lock_key = f"{name}:{user_key}"
        locks = getattr(self, "_external_ability_execution_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._external_ability_execution_locks = locks
        lock = locks.get(lock_key)
        if not isinstance(lock, asyncio.Lock):
            if len(locks) >= 512:
                for old_key, old_lock in list(locks.items()):
                    if isinstance(old_lock, asyncio.Lock) and not old_lock.locked():
                        locks.pop(old_key, None)
                    if len(locks) < 384:
                        break
            lock = asyncio.Lock()
            locks[lock_key] = lock
        async with lock:
            runtime = self._external_proactive_abilities.get(name)
            if not isinstance(runtime, dict) or not callable(runtime.get("executor")):
                return {
                    "success": False,
                    "context": "external：外部主动能力未注册或不可用",
                    "extra_components": [],
                    "summary": "外部能力不可用",
                    "effective_action": "message",
                }
            return await self._execute_external_proactive_ability_locked(
                name,
                runtime,
                user,
                display_name,
                reason,
            )

    async def _execute_external_proactive_ability_locked(
        self,
        name: str,
        runtime: dict[str, Any],
        user: dict[str, Any],
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        available = {
            self._normalize_external_ability_name(item.get("name"))
            for item in self._available_external_proactive_abilities(user)
            if isinstance(item, dict)
        }
        if name not in available:
            return {
                "success": False,
                "context": f"external:{name}：当前不可用或仍在冷却",
                "extra_components": [],
                "summary": "外部能力暂不可用",
                "effective_action": "message",
            }
        config = self._external_ability_config(name)
        call_context = {
            "user": dict(user or {}),
            "display_name": display_name,
            "reason": reason,
            "bot_name": runtime_persona_setting(self, "bot_name", "小星"),
            "state": deepcopy(self.data.get("daily_state", {})),
            "current_plan_item": deepcopy(self._proactive_current_plan_item(self.data.get("daily_plan", {})) or {}),
            "config": config,
            "plugin": self,
        }
        try:
            result = runtime["executor"](call_context)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            logger.warning("外部主动能力执行失败: %s: %s", name, exc, exc_info=True)
            self._note_external_ability_execution(
                name,
                user=user,
                success=False,
                status=f"执行失败: {exc}",
            )
            return {"success": False, "context": f"external:{name}：执行失败", "extra_components": [], "summary": "外部能力失败", "effective_action": f"external:{name}"}
        payload = result if isinstance(result, dict) else {"text": str(result or "")}
        success = bool(payload.get("ok", payload.get("success", True)))
        text = _single_line(payload.get("text"), 500)
        context = str(payload.get("context") or payload.get("summary") or text or "").strip()
        image_path = str(payload.get("image_path") or "").strip()
        extra_components = list(payload.get("extra_components") or []) if isinstance(payload.get("extra_components"), list) else []
        if image_path and os.path.exists(image_path):
            extra_components.extend(self._build_outbound_chain("", image_path))
        snapshot = payload.get("photo_snapshot") if isinstance(payload.get("photo_snapshot"), dict) else {}
        if success and image_path and os.path.exists(image_path) and snapshot:
            remember = getattr(self, "_remember_recent_photo_share_snapshot", None)
            if callable(remember):
                remember(
                    user,
                    caption=_single_line(snapshot.get("caption"), 260),
                    topic=_single_line(snapshot.get("topic"), 100),
                    motive=_single_line(snapshot.get("motive"), 180),
                    reason=_single_line(snapshot.get("reason"), 40) or name,
                    subject_owner=_single_line(snapshot.get("subject_owner"), 20),
                )
        memory = _single_line(payload.get("memory"), 500)
        if memory:
            user.setdefault("external_proactive_memory", [])
            memories = user.get("external_proactive_memory")
            if not isinstance(memories, list):
                memories = []
                user["external_proactive_memory"] = memories
            memories.append({"name": name, "ts": _now_ts(), "memory": memory})
            del memories[:-12]
        self._note_external_ability_execution(
            name,
            user=user,
            success=success,
            status=_single_line(payload.get("status") or context, 120),
            summary=_single_line(payload.get("summary") or text, 120),
        )
        return {
            "success": success,
            "context": f"external:{name}：{context or '外部能力已执行'}",
            "extra_components": extra_components,
            "summary": _single_line(payload.get("summary") or runtime.get("label") or name, 60),
            "effective_action": f"external:{name}",
        }

    def _note_external_ability_execution(
        self,
        name: str,
        *,
        user: dict[str, Any] | None = None,
        success: bool,
        status: str = "",
        summary: str = "",
    ) -> None:
        try:
            store = self._external_ability_store()
            item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
            executed_at = _now_ts()
            item["last_executed_ts"] = executed_at
            item["last_status"] = status
            item["last_summary"] = summary
            item["success_count"] = _safe_int(item.get("success_count"), 0, 0) + (1 if success else 0)
            item["failure_count"] = _safe_int(item.get("failure_count"), 0, 0) + (0 if success else 1)
            store[name] = item
            if isinstance(user, dict):
                user_last = user.setdefault("external_proactive_ability_last", {})
                if not isinstance(user_last, dict):
                    user_last = {}
                    user["external_proactive_ability_last"] = user_last
                user_last[name] = executed_at
            save_sections = {"external_proactive_abilities"}
            if isinstance(user, dict):
                save_sections.add("users")
            self._save_data_sync(sections=save_sections)
        except Exception:
            pass

    def _is_unusable_screen_peek_context(self, context: str) -> bool:
        text = str(context or "").strip()
        if not text:
            return True
        fail_tokens = (
            "screen_peek：失败",
            "屏幕插件不可用",
            "未授权",
            "不可用",
            "Invalid base64 image_url",
            "图片预处理结果为空",
            "所有视觉链路都失败",
            "视觉 provider 调用失败",
            "当前 provider 不支持原生视频上传",
            "没看清",
            "稍后再让我看看",
            "没有得到屏幕观察结果",
            "识屏分析失败",
        )
        return any(token in text for token in fail_tokens)

    def _is_screen_peek_provider_failure(self, context: str) -> bool:
        text = str(context or "")
        fail_tokens = (
            "Invalid base64 image_url",
            "图片预处理结果为空",
            "所有视觉链路都失败",
            "视觉 provider 调用失败",
            "Asset upload returned",
            "BadRequest",
            "InvalidParameter",
        )
        return any(token in text for token in fail_tokens)

    @staticmethod
    def _goodnight_screen_check_reply_matches(text: Any) -> bool:
        cleaned = _single_line(text, 240)
        if not cleaned:
            return False
        return bool(re.search(r"晚安|好梦|早点睡|睡吧|休息吧|明天见", cleaned))

    def _maybe_schedule_goodnight_screen_check(
        self,
        user: dict[str, Any],
        bot_reply: Any,
        *,
        now: float | None = None,
    ) -> bool:
        """Schedule one private screen check after a mutual goodnight."""
        if not bool(runtime_persona_setting(self, "enable_screen_glance_action", False)) or not bool(
            runtime_persona_setting(self, "enable_goodnight_screen_check", False)
        ):
            return False
        if not isinstance(user, dict) or not self._goodnight_screen_check_reply_matches(bot_reply):
            return False
        user_id = _single_line(user.get("user_id") or user.get("id"), 128)
        if not user_id or self._private_user_role(user, user_id) != "owner":
            return False
        umo = _single_line(user.get("umo"), 240)
        if not umo or ":FriendMessage:" not in umo or not user.get("enabled", True):
            return False

        rest_kind = _single_line(user.get("user_rest_kind"), 24).lower()
        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        rest_reason = _single_line(user.get("user_rest_reason"), 240)
        if rest_kind != "sleep" or rest_set_at <= 0:
            return False
        quiet_checker = getattr(self, "_user_rest_signal_should_block_current_reply", None)
        if callable(quiet_checker) and quiet_checker(rest_reason):
            return False

        check_now = _now_ts() if now is None else float(now)
        if check_now + 0.001 < rest_set_at or check_now - rest_set_at > 30 * 60:
            return False
        episode_key = f"{user_id}:{rest_set_at:.3f}"
        if _single_line(user.get("goodnight_screen_check_episode_key"), 180) == episode_key:
            return False
        if _single_line(user.get("goodnight_screen_check_checked_episode_key"), 180) == episode_key:
            return False

        delay_minutes = max(
            1,
            min(
                180,
                _safe_int(
                    runtime_persona_setting(
                        self, "goodnight_screen_check_delay_minutes", 45
                    ),
                    45,
                    1,
                    180,
                ),
            ),
        )
        user["goodnight_screen_check_due_at"] = check_now + delay_minutes * 60
        user["goodnight_screen_check_episode_at"] = rest_set_at
        user["goodnight_screen_check_episode_key"] = episode_key
        user["goodnight_screen_check_scheduled_at"] = check_now
        user["goodnight_screen_check_checked_at"] = 0
        user["goodnight_screen_check_state"] = "scheduled"
        return True

    def _goodnight_screen_check_block_reason(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        episode_at: float,
        now: float,
        require_screen: bool,
    ) -> str:
        if not bool(runtime_persona_setting(self, "enable_screen_glance_action", False)):
            return "screen_glance_disabled"
        if not bool(runtime_persona_setting(self, "enable_goodnight_screen_check", False)):
            return "goodnight_screen_check_disabled"
        if not isinstance(user, dict) or self._private_user_role(user, user_id) != "owner":
            return "not_primary_user"
        enabled_checker = getattr(self, "_user_enabled_for_proactive", None)
        if callable(enabled_checker) and not enabled_checker(user_id, user):
            return "private_proactive_disabled"
        umo = _single_line(user.get("umo"), 240)
        if not umo or ":FriendMessage:" not in umo:
            return "private_route_unavailable"
        generation_disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(generation_disabled) and generation_disabled(user):
            return "proactive_generation_disabled"

        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        if _single_line(user.get("user_rest_kind"), 24).lower() != "sleep" or abs(rest_set_at - episode_at) > 0.01:
            return "goodnight_episode_ended"
        rest_reason = _single_line(user.get("user_rest_reason"), 240)
        quiet_checker = getattr(self, "_user_rest_signal_should_block_current_reply", None)
        if callable(quiet_checker) and quiet_checker(rest_reason):
            return "explicit_do_not_disturb"
        latest_activity = max(
            _safe_float(user.get("last_activity_at"), 0),
            _safe_float(user.get("last_user_message_at"), 0),
        )
        if latest_activity > episode_at + 0.001:
            return "user_active_after_goodnight"
        rest_until_getter = getattr(self, "_user_rest_silence_until", None)
        if callable(rest_until_getter) and rest_until_getter(user, now=now) <= now:
            return "rest_window_ended"

        reset_daily = getattr(self, "_reset_daily_counter_if_needed", None)
        if callable(reset_daily):
            reset_daily(user)
        daily_limit_getter = getattr(self, "_effective_user_daily_limit", None)
        daily_limit = daily_limit_getter(user) if callable(daily_limit_getter) else 0
        unlimited_checker = getattr(self, "_proactive_daily_limit_is_unlimited", None)
        unlimited = bool(unlimited_checker(daily_limit)) if callable(unlimited_checker) else False
        if daily_limit <= 0 or (not unlimited and _safe_int(user.get("sent_today"), 0) >= daily_limit):
            return "daily_proactive_limit"

        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return "expression_decision_unavailable"
        try:
            decision = expression_builder(
                user,
                proactive_candidate={"eligible": True, "dynamic_allowance": daily_limit, "current_ts": now},
                message_intent={"requested_content_tier": "normal"},
                now=now,
            )
            expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
        except Exception:
            return "expression_decision_unavailable"
        if _single_line(expression.get("blocker"), 40):
            return f"expression_{_single_line(expression.get('blocker'), 40)}"
        if _safe_float(expression.get("proactive_cooldown_until"), 0) > now:
            return "expression_proactive_cooldown"
        if _safe_int(expression.get("proactive_budget"), 0, 0) <= 0:
            return "expression_proactive_budget_zero"
        if bool(user.get("proactive_sending")):
            return "another_proactive_message_is_sending"
        if require_screen and not self._screen_glance_available(user):
            return "screen_glance_unavailable"
        return ""

    @staticmethod
    def _goodnight_screen_check_prompt_document() -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.goodnight_screen_check",
                    title="晚安后屏幕状态判断",
                    source="proactive_message",
                    content=(
                        "这是一次用户已授权的晚安后单次状态确认，只用于决定是否需要轻声提醒休息。"
                        "请只判断当前画面是否能明确证明用户仍在主动使用电脑，不要转述或摘录任何屏幕内容。"
                        "active 仅用于存在明确持续操作或正在进行活动的证据；画面静止、锁屏、黑屏、无人操作、"
                        "证据不足或无法判断都输出 inactive 或 uncertain。"
                        '只输出 JSON：{"state":"active|inactive|uncertain","reason":"不含隐私的极短判断依据"}。'
                        "reason 禁止包含应用名、窗口名、账号、联系人、文件名、聊天内容、网页内容或屏幕文字。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
            ),
            metadata={"task": "goodnight_screen_check"},
        )

    @staticmethod
    def _goodnight_screen_check_history_text(name: str) -> str:
        section = prompt_section(
            key="background.goodnight_screen_check.history",
            title="晚安后单次确认历史问题",
            source="proactive_message",
            content=f"晚安后单次确认 {name or '用户'} 是否仍在主动使用电脑。",
        )
        return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)
