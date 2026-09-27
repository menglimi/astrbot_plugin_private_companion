# -*- coding: utf-8 -*-
"""群命令管理域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 167 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

from .command_handlers_shared import logger
from .group_command_system import (
    GROUP_COMMAND_HELP,
    GroupLLMAction,
    format_llm_blocked,
    format_llm_status,
    parse_group_command,
)
from .helpers import _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent



class CommandHandlersGroupCommandMixin:
    """群命令管理域（从 CommandHandlersMixin 拆出）。"""


    async def _group_command_management_allowed(
        self, event: AstrMessageEvent, group_id: str, *, refresh_role: bool = False
    ) -> bool:
        if self._can_manage_group_companion(event):
            return True
        if not refresh_role:
            return False
        refresher = getattr(self, "_refresh_group_role_snapshot", None)
        if callable(refresher):
            try:
                await refresher(event, group_id, force=False)
            except Exception as exc:
                logger.debug(
                    "刷新群权限快照失败: group=%s error=%s",
                    _single_line(group_id, 80),
                    _single_line(exc, 160),
                )
        return self._can_manage_group_companion(event)

    @staticmethod
    def _group_command_operator_id(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id())
        except (AttributeError, TypeError, ValueError):
            return ""

    async def _execute_group_llm_action(
        self, event: AstrMessageEvent, group_id: str, action: GroupLLMAction
    ) -> str:
        operator_id = self._group_command_operator_id(event)
        async with self._data_lock:
            if action is GroupLLMAction.BLOCK:
                item = self._set_group_llm_reply_block(
                    group_id, True, operator_id=operator_id, reason="group_command"
                )
                self._save_data_sync(sections={"group_llm_reply_blocks"})
                elapsed = (
                    self._format_timestamp_elapsed(_safe_float(item.get("updated_at"), 0.0, 0.0))
                    if item else "刚刚"
                )
                return format_llm_blocked(group_id, elapsed)
            if action is GroupLLMAction.RESTORE:
                self._set_group_llm_reply_block(
                    group_id, False, operator_id=operator_id, reason="group_command"
                )
                self._save_data_sync(sections={"group_llm_reply_blocks"})
                return "已恢复本群 LLM 回复。"
            item = self._group_llm_reply_block_item(group_id)
            blocked = bool(item.get("enabled"))
            elapsed = (
                self._format_timestamp_elapsed(_safe_float(item.get("updated_at"), 0.0, 0.0))
                if blocked else ""
            )
            return format_llm_status(blocked=blocked, elapsed=elapsed)

    async def _group_companion_command_impl(self, event: AstrMessageEvent):
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("这条命令需要在群聊里使用。")
            return
        request = parse_group_command(event.message_str)
        action = request.action
        response_chain = None
        if request.llm_action is not None:
            if not await self._group_command_management_allowed(event, group_id, refresh_role=True):
                yield event.plain_result(self._management_denied_text())
                return
            response = await self._execute_group_llm_action(event, group_id, request.llm_action)
            yield event.plain_result(response)
            event.stop_event()
            return
        if not runtime_persona_setting(self, 'enable_group_companion', True):
            yield event.plain_result(
                "群聊陪伴总开关当前关闭。\n"
                "这个群的名单和本群开关配置仍会保留，但暂时不会观察或参与群聊。\n"
                "请先在插件配置的群聊功能中开启“群聊陪伴”。"
            )
            return
        if not self._group_allowed_by_access_mode(group_id):
            if runtime_persona_setting(self, 'group_access_mode', 'whitelist') == "blacklist" and group_id in self._configured_group_blacklist_ids():
                yield event.plain_result("这个群在群聊陪伴黑名单中，暂时不启用。")
            elif runtime_persona_setting(self, 'group_access_mode', 'whitelist') == "whitelist":
                yield event.plain_result("这个群还没有加入群聊陪伴白名单，暂时不启用。")
            else:
                yield event.plain_result("这个群暂时不启用群聊陪伴。")
            return
        if request.requires_management and not await self._group_command_management_allowed(event, group_id):
            yield event.plain_result(self._management_denied_text())
            return
        async with self._data_lock:
            group = self._get_group(group_id)
            if action in {"开启", "启用", "打开"}:
                group["enabled"] = True
                self._save_data_sync(sections={"groups"})
                response = "群聊陪伴观察已开启。"
            elif action in {"关闭", "停用", "关掉"}:
                group["enabled"] = False
                self._save_data_sync(sections={"groups"})
                response = "群聊陪伴观察已关闭。"
            elif action in {"黑话", "梗", "词"}:
                slang = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
                meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
                if slang:
                    lines = ["当前群内常见词/梗："]
                    for item in slang[:20]:
                        if not isinstance(item, dict):
                            continue
                        term = _single_line(item.get("term"), 20)
                        if not term:
                            continue
                        meaning = ""
                        if isinstance(meanings.get(term), dict):
                            meaning_item = meanings[term]
                            confidence = min(1.0, _safe_float(meaning_item.get("confidence"), 1.0, 0.0))
                            raw_meaning = _single_line(meaning_item.get("meaning"), 60)
                            raw_usage = _single_line(meaning_item.get("usage"), 60)
                            if confidence >= 0.55 and not self._is_uncertain_group_slang_meaning(raw_meaning, raw_usage):
                                meaning = raw_meaning
                        lines.append(f"- {term}｜出现 {item.get('count', 0)} 次" + (f"｜{meaning}" if meaning else ""))
                    response = "\n".join(lines)
                else:
                    response = "还没有学到稳定的群内常见词。"
            elif action in {"群友", "成员", "画像"}:
                members = group.get("members") if isinstance(group.get("members"), dict) else {}
                ranked = sorted(
                    [item for item in members.values() if isinstance(item, dict)],
                    key=lambda item: _safe_int(item.get("count"), 0, 0),
                    reverse=True,
                )[:12]
                if ranked:
                    response = "当前群内成员观察：\n" + "\n".join(
                        f"- {_single_line(item.get('name'), 18) or '群友'}"
                        + (
                            "｜" + " / ".join(
                                _single_line(x, 18)
                                for x in (item.get('recent_phrases') or [])[:3]
                                if _single_line(x, 18)
                            )
                            if item.get("recent_phrases")
                            else ""
                        )
                        for item in ranked
                    )
                else:
                    response = "还没有群友样本。"
            elif action in {"话题", "线程"}:
                response = "当前群聊话题线程：\n" + (self._format_group_topic_threads_for_prompt(group) or "暂无。")
            elif action in {"片段", "群聊片段", "记忆"}:
                response = "近期群聊片段记忆：\n" + (self._format_group_episodes_for_prompt(group) or "暂无。")
            elif action in {"插话判定", "插话反馈", "反馈"}:
                response = "群聊插话反馈：" + self._format_group_interjection_feedback(group)
            elif action in {"关系网", "关系网络", "互动关系"}:
                response = "群友互动图：\n" + (self._format_group_relationship_graph_for_prompt(group) or "暂无。")
            elif action in {"撤回消息", "防撤回", "转述撤回", "撤回转述"}:
                if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_recall_transcribe_command', True):
                    response = "撤回消息转述没有开启。"
                else:
                    response = self._format_recalled_messages_for_event(event, limit=5)
                    extra_components = self._recalled_message_media_components_for_event(event, limit=5)
                    if extra_components:
                        response_chain = self._build_outbound_chain(response, extra_components=extra_components)
            elif action in {"状态", "气氛", ""}:
                response = self._format_group_status(group)
            else:
                response = GROUP_COMMAND_HELP
        if response_chain:
            yield event.chain_result(response_chain)
        else:
            yield event.plain_result(response)
        event.stop_event()
