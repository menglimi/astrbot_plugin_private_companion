# -*- coding: utf-8 -*-
"""ProactiveMessageActionExecutionPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_action_execution.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 441 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageActionExecutionMixin）。
"""
from __future__ import annotations

from .proactive_message_action_execution_shared import _now_ts, logger
from .proactive_message_action_execution_shared import Any
from .proactive_message_action_execution_shared import AstrMessageEvent
from .proactive_message_action_execution_shared import _safe_float
from .proactive_message_action_execution_shared import _single_line
from .proactive_message_action_execution_shared import _today_key
from .proactive_message_action_execution_shared import asyncio
from .proactive_message_action_execution_shared import random
from .proactive_message_action_execution_shared import runtime_persona_setting



class ProactiveMessageActionExecutionPart03Mixin:
    """ProactiveMessageActionExecutionPart03Mixin（从 ProactiveMessageActionExecutionMixin 拆出）。"""


    async def _maybe_run_pre_message_poke(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        *,
        action: str = "message",
        motive: str = "",
    ) -> tuple[int, str]:
        poke_count = self._choose_pre_message_poke_count(
            user,
            reason,
            action=action,
            motive=motive,
        )
        if poke_count <= 0:
            return 0, ""
        context = await self._run_poke_action(user, name, reason, explicit_count=poke_count)
        if not context.startswith("poke：已"):
            return 0, context
        return poke_count, context

    async def _run_voice_action(self, user: dict[str, Any], name: str, reason: str) -> dict[str, Any]:
        if not runtime_persona_setting(self, "enable_voice_action", False):
            return {"success": False, "context": "voice：未启用", "extra_components": [], "summary": "语音"}
        target = str(user.get("umo") or "").strip()
        if not target:
            return {"success": False, "context": "voice：缺少目标会话,无法发送语音", "extra_components": [], "summary": "语音"}
        voice_text = await self._build_voice_note_text(user, name, reason, target=target)
        touch_allowed = getattr(self, "_reality_touch_proactive_voice_allowed", lambda _: False)(user)
        components, audio_note = await self._create_voice_record_component(
            target,
            voice_text,
            defer_local_playback=touch_allowed,
        )
        if not components:
            return {
                "success": False,
                "context": (
                    "voice：语音生成失败\n"
                    f"想说的话：{voice_text}\n"
                    f"失败原因：{_single_line(audio_note, 160)}"
                ),
                "extra_components": [],
                "summary": "语音",
            }
        touch_player = getattr(self, "_mirror_reality_touch_proactive_voice", None)
        touched = bool(await touch_player(user, audio_note)) if touch_allowed and callable(touch_player) else False
        return {
            "success": True,
            "context": (
                "voice：已生成真实语音\n"
                f"语音内容：{self._strip_tts_markup(voice_text)}\n"
                f"真实语音文件：{audio_note}\n"
                f"现实触及：{'已同步到所选电脑音频设备' if touched else '未同步到电脑音频设备'}"
            ),
            "extra_components": components,
            "summary": "留了句语音",
        }

    def _resolve_aiocqhttp_client(self) -> Any:
        platform_manager = getattr(self.context, "platform_manager", None)
        platforms: list[Any] = []
        if platform_manager is not None:
            try:
                platforms = list(platform_manager.get_insts())
            except Exception:
                platforms = list(getattr(platform_manager, "platform_insts", []) or [])
        for platform in platforms:
            platform_names = set()
            try:
                meta = platform.meta()
                platform_names.add(str(getattr(meta, "id", "") or "").strip())
                platform_names.add(str(getattr(meta, "name", "") or "").strip())
            except Exception:
                pass
            platform_desc = f"{platform.__class__.__module__}.{platform.__class__.__name__}".lower()
            for attr in ("bot", "client", "_bot", "_client", "cqhttp"):
                client = getattr(platform, attr, None)
                client_desc = f"{client.__class__.__module__}.{client.__class__.__name__}".lower() if client is not None else ""
                if client is not None and (
                    "aiocqhttp" in platform_names
                    or "default(aiocqhttp)" in platform_names
                    or "aiocqhttp" in platform_desc
                    or "aiocqhttp" in client_desc
                    or (hasattr(client, "send_private_msg") and hasattr(client, "send_group_msg"))
                    or hasattr(client, "friend_poke")
                    or hasattr(client, "group_poke")
                ):
                    return client
        return None

    def _onebot_action_result_ok(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, dict):
            status = str(result.get("status") or result.get("result") or "").strip().lower()
            if status in {"failed", "fail", "error", "nok"}:
                return False
            retcode = result.get("retcode", result.get("code", None))
            if retcode is not None:
                try:
                    return int(retcode) == 0
                except Exception:
                    return False
        return True

    @staticmethod
    def _onebot_action_reported_success(action: str, result_or_error: Any) -> bool:
        if str(action or "").strip().lower() != "set_online_status":
            return False
        text = _single_line(result_or_error, 500).lower()
        return "set status success" in text or "set online status success" in text

    def _delivery_outcome_is_uncertain(self, error: Any) -> bool:
        if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        text = _single_line(error, 500).lower()
        return any(
            token in text
            for token in (
                "timed out",
                "timeout",
                "deadline exceeded",
                "read timeout",
                "write timeout",
                "connection reset",
                "connection closed",
                "server disconnected",
                "remote disconnected",
                "broken pipe",
                "回执超时",
                "响应超时",
                "连接被重置",
                "连接已关闭",
            )
        )

    def _log_uncertain_onebot_submission(self, action: str, error: Any) -> None:
        logger.warning(
            "OneBot 动作回执不确定，为避免同一内容被别名立即重复提交，本次按已提交处理: action=%s error=%s",
            action,
            self._format_send_exception(error),
        )

    async def _call_onebot_action(self, client: Any, action: str, **params: Any) -> bool:
        ok, _ = await self._call_onebot_action_with_error(client, action, **params)
        return ok

    async def _call_onebot_action_with_error(
        self,
        client: Any,
        action: str,
        *,
        at_most_once: bool = False,
        **params: Any,
    ) -> tuple[bool, str]:
        candidates = (
            "call_action",
            "call_api",
            "api",
        )
        last_error = ""
        for attr in candidates:
            func = getattr(client, attr, None)
            if not callable(func):
                continue
            try:
                result = func(action, **params)
            except TypeError:
                try:
                    result = func(action, params)
                except Exception as exc:
                    if self._onebot_action_reported_success(action, exc):
                        return True, "协议端已设置状态"
                    if self._is_onebot_event_checker_send_rejection(exc):
                        return False, self._onebot_event_checker_rejection_summary()
                    if at_most_once and self._delivery_outcome_is_uncertain(exc):
                        self._log_uncertain_onebot_submission(action, exc)
                        return True, "回执不确定，已停止立即重试"
                    last_error = self._format_send_exception(exc)
                    if at_most_once:
                        return False, last_error
                    continue
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if self._is_onebot_event_checker_send_rejection(exc):
                    return False, self._onebot_event_checker_rejection_summary()
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                last_error = self._format_send_exception(exc)
                if at_most_once:
                    return False, last_error
                continue
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if self._is_onebot_event_checker_send_rejection(exc):
                    return False, self._onebot_event_checker_rejection_summary()
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                last_error = self._format_send_exception(exc)
                if at_most_once:
                    return False, last_error
                continue
            if self._onebot_action_result_ok(result) or self._onebot_action_reported_success(action, result):
                return True, ""
            last_error = f"{attr} 返回失败: {_single_line(result, 180)}"
            if self._is_onebot_event_checker_send_rejection(result):
                return False, self._onebot_event_checker_rejection_summary()
            if at_most_once:
                return False, last_error
        func = getattr(client, action, None)
        if callable(func):
            try:
                result = func(**params)
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                return False, self._format_send_exception(exc)
            try:
                if hasattr(result, "__await__"):
                    result = await result
            except Exception as exc:
                if self._onebot_action_reported_success(action, exc):
                    return True, "协议端已设置状态"
                if at_most_once and self._delivery_outcome_is_uncertain(exc):
                    self._log_uncertain_onebot_submission(action, exc)
                    return True, "回执不确定，已停止立即重试"
                return False, self._format_send_exception(exc)
            if self._onebot_action_result_ok(result) or self._onebot_action_reported_success(action, result):
                return True, ""
            return False, f"{action} 返回失败: {_single_line(result, 180)}"
        return False, last_error or f"OneBot 客户端不支持动作 {action}"

    def _input_status_user_id_from_umo(self, umo: str) -> str:
        if not umo or ":FriendMessage:" not in str(umo):
            return ""
        platform_supports = getattr(self, "_platform_supports", None)
        if callable(platform_supports) and not platform_supports("input_status", umo=umo):
            return ""
        session = self._parse_message_session(umo)
        if not session:
            return ""
        user_id = str(getattr(session, "session_id", "") or "").strip()
        return user_id if user_id.isdigit() else ""

    def _prune_last_input_status_at(self, now: float) -> None:
        cache = getattr(self, "_last_input_status_at", None)
        if not isinstance(cache, dict) or not cache:
            return
        ttl = 24 * 3600
        stale = [k for k, v in cache.items() if now - _safe_float(v, 0) >= ttl]
        for k in stale:
            cache.pop(k, None)
        max_items = 512
        if len(cache) > max_items:
            for k in sorted(cache, key=cache.get)[: len(cache) - max_items]:
                cache.pop(k, None)

    async def _send_input_status_once(self, user_id: str, *, client: Any | None = None) -> bool:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit():
            return False
        if client is None:
            client = self._resolve_aiocqhttp_client()
        if client is None:
            return False
        variants = (
            {"user_id": int(user_id), "event_type": 1},
            {"user_id": int(user_id), "status": 1},
            {"user_id": int(user_id), "typing": True},
        )
        for params in variants:
            if await self._call_onebot_action(client, "set_input_status", **params):
                self._prune_last_input_status_at(_now_ts())
                self._last_input_status_at[user_id] = _now_ts()
                return True
        return False

    async def _maybe_send_input_status(self, umo: str, text: str = "") -> None:
        user_id = self._input_status_user_id_from_umo(umo)
        if not user_id:
            return
        now = _now_ts()
        self._prune_last_input_status_at(now)
        last_at = _safe_float(self._last_input_status_at.get(user_id), 0)
        if now - last_at < 45:
            return
        duration = max(1.2, min(4.5, len(str(text or "")) / 18))
        if not await self._send_input_status_once(user_id):
            return
        self._last_input_status_at[user_id] = now
        await asyncio.sleep(random.uniform(duration * 0.55, duration))

    async def _passive_input_status_loop(self, user_id: str, *, max_seconds: float = 90.0) -> None:
        user_id = str(user_id or "").strip()
        if not user_id.isdigit():
            return
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return
        started_at = _now_ts()
        while not bool(getattr(self, "_stop_event", asyncio.Event()).is_set()):
            if _now_ts() - started_at > max_seconds:
                return
            try:
                await self._send_input_status_once(user_id, client=client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("私聊输入状态刷新失败: %s", _single_line(exc, 120))
                return
            await asyncio.sleep(random.uniform(3.2, 4.8))

    def _start_passive_input_status_loop(self, event: AstrMessageEvent, user_id: str = "") -> None:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        parsed_user_id = self._input_status_user_id_from_umo(umo)
        # ``user_id`` is normally the platform-scoped profile storage key.  It
        # may contain a namespace/digest for a QQ-official event, while the
        # OneBot transport still requires the numeric sender from the UMO.
        storage_user_id = str(user_id or parsed_user_id or "").strip()
        if not parsed_user_id or not parsed_user_id.isdigit():
            return
        task_key = storage_user_id or parsed_user_id
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
            self._passive_input_status_tasks = tasks
        old_task = tasks.get(task_key)
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            old_task.cancel()
        task = asyncio.create_task(self._passive_input_status_loop(parsed_user_id))
        tasks[task_key] = task
        try:
            # Keep the scoped key on the event so stop/cleanup remains
            # isolated, but expose the numeric transport ID for diagnostics.
            setattr(event, "private_companion_input_status_user_id", task_key)
            setattr(event, "private_companion_input_status_transport_id", parsed_user_id)
        except Exception:
            pass

        def _cleanup(done_task: asyncio.Task) -> None:
            current = tasks.get(task_key)
            if current is done_task:
                tasks.pop(task_key, None)

        task.add_done_callback(_cleanup)

    def _stop_passive_input_status_loop(self, event_or_user: Any) -> None:
        user_id = ""
        if isinstance(event_or_user, str):
            user_id = event_or_user.strip()
        else:
            user_id = str(getattr(event_or_user, "private_companion_input_status_user_id", "") or "").strip()
            if not user_id:
                try:
                    user_id = str(event_or_user.get_sender_id()).strip()
                except Exception:
                    user_id = ""
        if not user_id:
            return
        tasks = getattr(self, "_passive_input_status_tasks", None)
        if not isinstance(tasks, dict):
            return
        task = tasks.pop(user_id, None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()

    def _qq_presence_codes(self, mode: str) -> tuple[int, int, str]:
        normalized = str(mode or "").strip().lower()
        table = {
            "online": (10, 0, "在线"),
            "away": (30, 0, "离开"),
            "busy": (50, 0, "忙碌"),
            "invisible": (40, 0, "隐身"),
        }
        return table.get(normalized, table["online"])

    async def _set_qq_online_presence(self, mode: str) -> tuple[bool, str]:
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "未找到可用 QQ 客户端"
        status, ext_status, label = self._qq_presence_codes(mode)
        ok, error = await self._call_onebot_action_with_error(
            client,
            "set_online_status",
            at_most_once=True,
            status=status,
            ext_status=ext_status,
            battery_status=0,
        )
        if ok:
            return True, label
        return False, f"平台不支持 set_online_status：{label}（{_single_line(error, 100)}）"

    async def _set_qq_custom_presence(self, text: str) -> tuple[bool, str]:
        if not getattr(self, "enable_qq_custom_presence_sync", False):
            return False, "QQ 自定义短状态未开启"
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return False, "未找到可用 QQ 客户端"
        custom_text = _single_line(text, 8)
        if not custom_text:
            return False, "自定义状态文本为空,跳过同步"
        # Avoid set_custom_online_status: some OneBot adapters disconnect on this unsupported extension API.
        ok, error = await self._call_onebot_action_with_error(
            client,
            "set_diy_online_status",
            at_most_once=True,
            face_id=21,
            face_type=1,
            wording=custom_text,
        )
        if ok:
            return True, f"自定义状态：{custom_text}"
        return False, f"平台不支持自定义状态：{custom_text}（{_single_line(error, 100)}）"

    async def _reset_stale_qq_presence_if_needed(self) -> None:
        if not self.enable_qq_presence_sync:
            return
        await asyncio.sleep(2)
        try:
            await self._ensure_current_detail_presence_status()
        except Exception as exc:
            logger.debug("启动同步当前 QQ 状态失败: %s", exc)
        async with self._data_lock:
            state = self.data.get("qq_presence_state", {})
            if not isinstance(state, dict) or str(state.get("date") or "") == _today_key():
                return
            previous_mode = str(state.get("mode") or "")
        ok, note = await self._set_qq_online_presence("online")
        async with self._data_lock:
            state = self.data.setdefault("qq_presence_state", {})
            if not isinstance(state, dict):
                state = {}
                self.data["qq_presence_state"] = state
            state.update(
                {
                    "date": _today_key(),
                    "plan_date": "",
                    "detail_key": "",
                    "mode": "online",
                    "custom_text": "",
                    "reason": "清理跨日 QQ 状态",
                    "updated_at": _now_ts(),
                    "ok": bool(ok),
                    "note": _single_line(f"跨日重置：{previous_mode or 'unknown'} -> {note}", 120),
                }
            )
            self._save_data_sync(sections={"qq_presence_state"})
