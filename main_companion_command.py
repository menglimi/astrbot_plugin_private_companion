# -*- coding: utf-8 -*-
"""陪伴指令域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（8 个方法 / 369 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import os
import re
from .helpers import _now_ts, _path_text, _single_line, _today_key
from .main_shared import (
    bookshelf_password_reset_actions,
    companion_manual_query_actions,
    daily_outfit_generate_actions,
    daily_schedule_cancel_actions,
    daily_schedule_regenerate_actions,
    image_api_swap_actions,
    photo_command_actions,
    qweather_location_actions,
    wakeup_alarm_actions,
)
from .persona_config import runtime_persona_setting
from .planning import generate_detail_enhancement
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginCompanionCommandMixin:
    """陪伴指令域（从 PrivateCompanionPlugin 拆出）。"""

    async def _companion_command_bootstrap_private_identity(self, event: Any, is_private: Any) -> None:
        """私聊陪伴指令进入前，确保私聊身份档案已建立并完成 REQ036 上下文挂载。"""
        if is_private:
            raw_user_id = str(event.get_sender_id() or "").strip()
            identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
            user_id = identity_normalizer(raw_user_id) if callable(identity_normalizer) else raw_user_id
            user_id = user_id or raw_user_id
            sender_name_reader = getattr(self, "_sender_display_name", None)
            if callable(sender_name_reader):
                sender_display_name = _single_line(sender_name_reader(event), 40)
            else:
                sender_display_name = _single_line(user_id, 40)
            async with self._data_lock:
                private_user, _ = self._ensure_auto_private_user_profile(
                    event,
                    user_id=user_id,
                    sender_display_name=sender_display_name,
                    now=_now_ts(),
                )
                if isinstance(private_user, dict):
                    user_id = _single_line(private_user.get("user_id"), 160) or user_id
                migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
                if callable(migrator):
                    migrator(user_id, private_user)
                self._req036_attach_unified_profile_context(
                    event,
                    user=private_user if isinstance(private_user, dict) else None,
                    source="private_command",
                )
                self._schedule_data_save(sections={"users", "unified_person"})

    def _companion_manual_inline_action(self, action: Any, value: Any, query_actions: Any) -> Any:
        """把「答疑 <内联指令>」的写法归一到具体的 manual 动作与参数。"""
        if action in companion_manual_query_actions:
            inline_value = value.strip()
            if inline_value in {"确认", "应用", "执行", "确认执行", "应用建议"}:
                action = "答疑确认"
                value = ""
            elif inline_value in {"取消", "取消建议", "放弃"}:
                action = "答疑取消"
                value = ""
            else:
                inline_parts = inline_value.split(maxsplit=1)
                if len(inline_parts) >= 2 and inline_parts[0] in {"设置", "修改", "set", "Set", "SET"}:
                    action = "答疑设置"
                    value = inline_parts[1].strip()
                elif re.search(r"^[A-Za-z_][A-Za-z0-9_]*\s*(?:=|:|：|设为|设置为|改成|调到)\s*\S+", inline_value):
                    action = "答疑设置"
                    value = inline_value
                else:
                    maybe_key, maybe_value = self._companion_manual_parse_setting_text(inline_value)
                    if maybe_key and maybe_value:
                        ok, _, _ = self._companion_manual_normalize_config_value(maybe_key, maybe_value)
                        if ok:
                            action = "答疑设置"
                            value = inline_value
        return action, value

    def _companion_command_resolve_user_id(self, event: Any) -> Any:
        """解析陪伴指令的事件身份，返回 (原始 ID, 规范化 ID)。"""
        raw_user_id = str(event.get_sender_id() or "").strip()
        resolver = getattr(self, "_private_user_id_for_event", None)
        canonicalizer = getattr(self, "_canonical_private_user_id", None)
        identity_normalizer = getattr(self, "_normalize_private_identity_id", None)
        fallback_user_id = (
            identity_normalizer(raw_user_id)
            if callable(identity_normalizer)
            else raw_user_id
        ) or raw_user_id
        user_id = (
            resolver(event, raw_user_id)
            if callable(resolver)
            else canonicalizer(fallback_user_id) if callable(canonicalizer) else fallback_user_id
        )
        user_id = _single_line(user_id, 160) or raw_user_id
        return raw_user_id, user_id

    def _format_companion_status_response(self, user: Any, user_id: Any) -> str:
        """生成「状态 / status」指令的运行状态总览文本。"""
        self._reset_daily_counter_if_needed(user)
        last_seen = self._format_timestamp_elapsed(self._latest_user_activity_ts(user))
        last_sent = self._format_timestamp_elapsed(user.get("last_sent"))
        plan = self.data.get("daily_plan", {})
        plan_text = self._format_plan_status_summary(plan if isinstance(plan, dict) else {})
        state = self.data.get("daily_state", {})
        state_text = (
            f"{state.get('date')}｜能量 {state.get('energy', 70)}/100｜情绪偏{state.get('mood_bias', '平稳')}"
            if state else "未生成"
        )
        simulation_text = self._format_simulation_summary(user)
        return "".join(
            [
                "运行模式：默认开启\n",
                f"称呼：{user.get('nickname') or runtime_persona_setting(self, 'default_nickname', '你')}\n",
                f"语气：{user.get('style') or runtime_persona_setting(self, 'default_style', '温柔')}\n",
                f"日程：{plan_text}\n",
                f"拟人状态：{state_text}\n",
                f"关系角色：{self._private_user_role_label(self._private_user_role(user, user_id))}\n",
                f"今日主动消息：{user.get('sent_today', 0)}/{self._effective_user_daily_limit(user)}\n",
                f"今日软目标：约 {self._soft_daily_target(user):.1f} 条\n",
                f"免打扰：{runtime_persona_setting(self, 'quiet_hours', '23:00-08:30')}\n",
                f"上次活跃：{last_seen}\n",
                f"上次主动：{last_sent}\n",
                f"下次候选：{self._format_next_proactive(user)}\n",
                f"{simulation_text}\n" if simulation_text else "",
                f"{self._format_suspended_summary(user)}\n",
                f"主动方式承接：{self._format_action_affinity_summary(user)}\n",
                f"关系：{self._format_relationship_summary(user)}",
            ]
        )

    async def _companion_command_dispatch_actions(self, event: Any, user: Any, user_id: Any, action: Any, value: Any, wakeup_test_requested: Any) -> bool:
        """陪伴指令尾段：摄像头 / 闹钟测试 / 答疑 / 城市 / 生图 / 生图接口切换。返回 True 表示已收口。"""
        if (
            action in wakeup_alarm_actions
            and isinstance(wakeup_test_requested, dict)
            and wakeup_test_requested.get("camera_snapshot")
        ):
            camera_snapshotter = getattr(self, "_reality_touch_camera_snapshot_for_user", None)
            try:
                result = (
                    await camera_snapshotter(user_id, wakeup_test_requested.get("purpose"))
                    if callable(camera_snapshotter)
                    else {"status": "unavailable", "message": "当前插件实例没有摄像头单帧能力"}
                )
            except Exception as exc:
                logger.warning(
                    "摄像头单帧读取异常: %s",
                    _single_line(exc, 160),
                )
                result = {"status": "error", "message": "摄像头单帧读取失败，请稍后再试或检查设备连接。"}
            observation = result.get("observation") if isinstance(result.get("observation"), dict) else {}
            detail = _single_line(observation.get("summary"), 180)
            await self._reply(
                event,
                ("单帧读取完成：" + detail) if result.get("status") == "success" and detail else _single_line(result.get("message"), 200),
            )
            event.stop_event()
            return True
        if action in wakeup_alarm_actions and wakeup_test_requested:
            self._create_lifecycle_background_task(
                self._test_wakeup_alarm(user),
                label="wakeup_alarm_test",
            )
            event.stop_event()
            return True
        if action in companion_manual_query_actions:
            await self._reply(event, await self._companion_manual_answer(event, value))
            event.stop_event()
            return True
        if action in qweather_location_actions:
            await self._reply(event, await self._qweather_location_command_text(action, value))
            event.stop_event()
            return True
        if action in photo_command_actions:
            await self._handle_companion_photo_command(event, user_id, action, value)
            return True
        if action in image_api_swap_actions:
            force_swap = bool(re.search(r"(?:强制|force|确认|直接)", value, flags=re.I))
            await self._reply(event, await self._swap_external_image_api_command_text(force=force_swap))
            event.stop_event()
            return True
        return False

    async def _companion_command_qzone_actions(self, event: Any, response: Any, action: Any, value: Any) -> bool:
        """陪伴指令尾段：QQ 空间发布与链路自检、AI 日报 / 新闻。返回 True 表示已收口。"""
        if action in {"发说说", "发QQ空间", "发布说说", "空间发布", "发布空间"}:
            image_sources = await self._qzone_image_sources_from_event(event)
            image_sources, image_select_message = self._qzone_select_image_sources(value, image_sources)
            if image_select_message:
                await self._reply(event, image_select_message)
                event.stop_event()
                return True
            publish_text = self._qzone_clean_publish_text(value)
            if image_sources and publish_text in {"[图片]", "【图片】", "图片"}:
                publish_text = ""
            if not publish_text and not image_sources:
                await self._reply(event, "请这样使用：陪伴 发说说 <正文>，也可以随消息附带图片。\n这是公开发布动作，正文或图片不能为空。")
                event.stop_event()
                return True
            await self._reply(event, response)
            result = await self._publish_qzone_text(publish_text, event, images=image_sources, auto_generate_image=True)
            if result.get("success"):
                await self._reply(
                    event,
                    "QQ 空间说说已发布。\n"
                    f"QQ：{result.get('uin') or '未知'}\n"
                    f"tid：{result.get('tid') or '未知'}\n"
                    f"正文：{_single_line(result.get('text'), 160) or '无'}\n"
                    f"图片：{len(result.get('images') or [])} 张\n"
                    f"校验：{_single_line(result.get('verify_message'), 120) or ('通过' if result.get('verified') else '未校验')}",
                )
            else:
                await self._reply(event, f"发布失败：{_single_line(result.get('message'), 180)}")
            event.stop_event()
            return True
        if action in {"测试说说链路", "测试空间发布", "测试QQ空间发布", "测试qzone发布"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_tool_chain(event))
            event.stop_event()
            return True
        if action in {"测试说说配图", "测试空间配图", "测试QQ空间配图", "测试qzone配图"}:
            await self._reply(event, response)
            await self._reply(event, await self._test_qzone_publish_image_chain(event))
            event.stop_event()
            return True
        if action in {"AI日报", "ai日报", "日报", "AI早报", "ai早报", "早报"}:
            await self._reply(event, response)
            await self._maybe_track_ai_daily(force=True)
            await self._reply(event, self._format_ai_daily_digest_for_command())
            event.stop_event()
            return True
        if action in {"新闻", "今日新闻", "AI新闻", "ai新闻"}:
            await self._reply(event, response)
            await self._perform_news_reading(reason="user_query", allow_share=False, force=True)
            await self._reply(event, self._format_news_digest_for_command())
            event.stop_event()
            return True
        return False

    async def _companion_command_reset_actions(self, event: Any, response: Any, action: Any, value: Any) -> None:
        """陪伴指令尾段：夹层密码重置、人格 / 插件重置、日程重生成与取消。"""
        if action in bookshelf_password_reset_actions:
            await self._reply(event, response)
        if action in {"重置当前人格", "当前人格重置", "重置人格"}:
            result = await self._reset_current_persona_store(rebuild_today=True)
            if not result.get("ok"):
                await self._reply(event, result.get("message") or "当前人格重置失败。")
            else:
                persona_label = result.get("persona_id") or "当前单人格资料"
                generation = result.get("generation") or 1
                rebuild_error = _single_line(result.get("rebuild_error"), 180)
                message = (
                    f"当前人格已重置：{persona_label}\n"
                    f"人格资料代次：第 {generation} 代\n"
                    "插件基础配置、多人格列表和窗口绑定均已保留。\n"
                    "重置前资料已保存到 persona_backups。同步到 MemoryCompanion 的当前人格分域投影会一并清理；"
                    "AstrBot 会话历史和 MemoryCompanion 自主管理的其他长期记忆不受影响。"
                )
                if rebuild_error:
                    message += f"\n今日状态与日程自动重建失败：{rebuild_error}"
                else:
                    state = result.get("state") if isinstance(result.get("state"), dict) else {}
                    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
                    if state:
                        message += "\n\n" + self._format_state_detail(state)
                    if plan:
                        message += "\n\n" + self._format_daily_plan(plan)
                await self._reply(event, message)
        if action in {"重置插件", "全部重置"}:
            await self._reset_plugin_store()
            state, plan, _ = await self._rebuild_today_after_reset()
            await self._reply(
                event,
                "插件状态已清空并重建。\n"
                + self._format_state_detail(state)
                + "\n\n"
                + self._format_daily_plan(plan or {}),
            )
        if action in daily_schedule_regenerate_actions:
            if value:
                ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                    value,
                    generate_detail_enhancement,
                )
                if ok and isinstance(detail, dict):
                    summary = _single_line(detail.get("summary"), 140)
                    if summary:
                        message = f"{message}\n{summary}"
                await self._reply(event, message)
            else:
                plan = await self._ensure_daily_plan(force=True)
                async with self._data_lock:
                    self.data["detail_enhanced_day"] = str((plan or {}).get("date") or _today_key())
                    self.data["detail_enhanced_segments"] = {}
                    self.data["daily_story_plan"] = {}
                    self._save_data_sync(
                        sections={
                            "daily_plan",
                            "daily_story_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                        }
                    )
                await self._reply(event, self._format_daily_plan(plan or {}))
        if action in daily_schedule_cancel_actions:
            _, message = await self._cancel_daily_plan_segment_by_selector(value)
            await self._reply(event, message)

    async def _companion_command_generate_actions(self, event: Any, user: Any, action: Any, value: Any) -> bool:
        """陪伴指令尾段：穿搭 / 状态 / 提示词 / 细化 / 日记 / 梦境。返回 True 表示已收口。"""
        if action in daily_outfit_generate_actions:
            outfit_generator = getattr(self, "_ensure_daily_outfit_photo", None)
            outfit_lock = getattr(self, "_daily_outfit_photo_generation_lock", None)
            wait_existing = bool(outfit_lock is not None and outfit_lock.locked())
            if wait_existing:
                await self._reply(event, "穿搭图已经在生成中了，我等这轮结果出来直接发给你。")
                outfit = await outfit_generator(force=False) if callable(outfit_generator) else None
            else:
                await self._reply(event, "等我换身衣服哦")
                plan = await self._ensure_daily_plan(force=False)
                if not plan:
                    plan = await self._ensure_daily_plan(force=True)
                outfit = await outfit_generator(force=True) if callable(outfit_generator) else None
            if isinstance(outfit, dict) and outfit.get("path"):
                image_path = _path_text(outfit.get("path"), 1000)
                if not os.path.exists(image_path):
                    await self._reply(event, f"每日穿搭照片未生成：图片文件不存在 {image_path}")
                    event.stop_event()
                    return True
                caption = "换好啦，你看"
                if not self._should_skip_recent_outfit_command_send(event, text=caption, image_path=image_path):
                    try:
                        await self._reply_with_optional_media(event, caption, image_path)
                    except Exception as exc:
                        logger.warning(
                            "每日穿搭命令发图异常,为避免重复发送已不再兜底补发: image=%s err=%s",
                            _single_line(image_path, 160),
                            _single_line(exc, 180),
                        )
            else:
                error = _single_line((outfit or {}).get("error") if isinstance(outfit, dict) else "", 180)
                note = _single_line((outfit or {}).get("note") if isinstance(outfit, dict) else "", 180)
                await self._reply(event, f"每日穿搭照片未生成：{error or note or '没有可用结果'}")
        if action in {"生成状态", "刷新状态", "重生状态"}:
            state = await self._ensure_daily_state(force=True)
            async with self._data_lock:
                self.data["daily_plan"] = {}
                self._save_data_sync(sections={"daily_plan"})
            await self._reply(
                event,
                self._format_state_detail(state)
                + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
            )
        if action in {"增添状态", "添加状态"}:
            ok, message = await self._add_manual_state(value)
            if ok:
                async with self._data_lock:
                    self.data["daily_plan"] = {}
                    state = dict(self.data.get("daily_state", {}))
                    self._save_data_sync(sections={"daily_plan"})
                await self._reply(
                    event,
                    message
                    + "\n"
                    + self._format_state_detail(state)
                    + "\n今天的日程已清空,下次生成日程会按这个状态重新安排。",
                )
            else:
                await self._reply(event, message)
        if action in {"查看提示词", "提示词", "prompt"}:
            prompt_text = await self._debug_prompt_text(value or "主动", user, event)
            await self._reply(event, prompt_text)
        if action in {"重置细化"}:
            plan = await self._ensure_daily_plan(force=False)
            if not plan:
                plan = await self._ensure_daily_plan(force=True)
            ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                "当前",
                generate_detail_enhancement,
                reason="用户通过聊天命令重置当前日程细化",
            )
            if ok:
                detail_text = self._format_current_detail_view()
                if detail_text:
                    message = f"{message}\n{detail_text}"
            await self._reply(event, message)
        if action in {"生成日记", "刷新日记"}:
            diary = await self._ensure_daily_diary(force=True)
            await self._reply(event, self._format_single_diary(diary or {}))
        if action in {"梦境", "做了什么梦", "今日梦境"}:
            state = await self._ensure_daily_state(force=False)
            if not state:
                state = await self._ensure_daily_state(force=True)
            await self._reply(event, self._format_dream_view(state or {}))
        return False
