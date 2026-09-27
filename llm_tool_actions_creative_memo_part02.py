# -*- coding: utf-8 -*-
"""LlmToolActionsCreativeMemoPart02Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_creative_memo.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 401 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsCreativeMemoMixin）。
"""
from __future__ import annotations

import json
import re
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _safe_int, _single_line
from .llm_tool_actions_shared import _render_tool_prompt_section_labeled, logger
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsCreativeMemoPart02Mixin:
    """LlmToolActionsCreativeMemoPart02Mixin（从 LlmToolActionsCreativeMemoMixin 拆出）。"""


    async def _pc_view_creative_work_impl(
        self,
        event: AstrMessageEvent,
        *,
        action: str = "get",
        selector: str = "",
        part: int = 0,
        max_chars: int = 6000,
    ) -> str:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return json.dumps(
                {"status": "forbidden", "message": "创作正文只允许在私聊中读取。"},
                ensure_ascii=False,
            )

        normalized_action = _single_line(action, 20).lower() or "get"
        if normalized_action not in {"list", "get"}:
            return json.dumps(
                {"status": "invalid_action", "message": "action 仅支持 list/get。"},
                ensure_ascii=False,
            )
        async with self._data_lock:
            raw_projects = self.data.get("creative_projects")
            projects = list(raw_projects) if isinstance(raw_projects, list) else []
            eligible = self._creative_work_project_candidates(projects, "")
            if normalized_action == "list":
                summaries = [
                    self._creative_work_project_summary(project, index)
                    for index, project in enumerate(eligible, start=1)
                ]
                return json.dumps(
                    {
                        "status": "success",
                        "action": "list",
                        "count": len(summaries),
                        "projects": summaries[-20:],
                        "bookshelf": self._bookshelf_inventory_snapshot(event, projects),
                        "instruction": "直接依据这份真实库存回答，不要写查找动作，也不要把未列出的内容补成存在。",
                    },
                    ensure_ascii=False,
                )

            matches = self._creative_work_project_candidates(projects, selector)
            if not _single_line(selector, 120):
                matches = eligible[-1:] if eligible else []
            if not matches:
                candidates = [
                    self._creative_work_project_summary(project, index)
                    for index, project in enumerate(eligible[-10:], start=max(1, len(eligible) - 9))
                ]
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "没有找到对应的创作。",
                        "selector": _single_line(selector, 120),
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                )
            if len(matches) > 1:
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多篇创作，请使用准确标题或 id 再读取。",
                        "candidates": [
                            self._creative_work_project_summary(project, index)
                            for index, project in enumerate(matches[:10], start=1)
                        ],
                    },
                    ensure_ascii=False,
                )

            project = matches[0]
            chunks = [
                chunk
                for chunk in project.get("draft_chunks", [])
                if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
            ]
            requested_part = _safe_int(part, 0, 0)
            if part and not (1 <= requested_part <= len(chunks)):
                return json.dumps(
                    {
                        "status": "part_not_found",
                        "message": f"这篇创作目前只有 {len(chunks)} 个正文部分。",
                        "title": _single_line(project.get("title"), 80) or "未定标题",
                        "part_count": len(chunks),
                    },
                    ensure_ascii=False,
                )

            budget = _safe_int(max_chars, 6000, 600, 12000)
            selected_parts: list[dict[str, Any]] = []
            used_chars = 0
            start_index = requested_part - 1 if requested_part > 0 else 0
            for index in range(start_index, len(chunks)):
                if requested_part > 0 and index != start_index:
                    break
                text_value = str(chunks[index].get("text") or "").strip()
                remaining = budget - used_chars
                if remaining <= 0:
                    break
                shown_text = text_value[:remaining]
                selected_parts.append(
                    {
                        "part": index + 1,
                        "text": shown_text,
                        "chars": len(text_value),
                        "truncated": len(shown_text) < len(text_value),
                    }
                )
                used_chars += len(shown_text)
                if len(shown_text) < len(text_value):
                    break
            last_part = selected_parts[-1]["part"] if selected_parts else 0
            truncated = bool(
                selected_parts
                and (
                    selected_parts[-1].get("truncated")
                    or (requested_part == 0 and last_part < len(chunks))
                )
            )
            payload = {
                "status": "success",
                "action": "get",
                "project": self._creative_work_project_summary(project),
                "premise": _single_line(project.get("premise"), 500),
                "tone": _single_line(project.get("tone"), 120),
                "parts": selected_parts,
                "truncated": truncated,
                "next_part": last_part + 1 if truncated and last_part < len(chunks) else 0,
                "instruction": "只能依据返回的真实正文讨论，不要补写未读取内容。",
            }
            return json.dumps(payload, ensure_ascii=False)

    def _strip_plaintext_tool_call_envelopes(self, text: Any) -> tuple[str, list[dict[str, Any]]]:
        raw = str(text or "")
        if not raw or "{" not in raw:
            return raw, []
        decoder = json.JSONDecoder()
        calls: list[dict[str, Any]] = []
        ranges: list[tuple[int, int]] = []
        cursor = 0
        while cursor < len(raw):
            start = raw.find("{", cursor)
            if start < 0:
                break
            try:
                value, consumed = decoder.raw_decode(raw[start:])
            except Exception:
                cursor = start + 1
                continue
            end = start + consumed
            call = self._plaintext_tool_call_from_object(value)
            if call is None:
                cursor = start + 1
                continue
            calls.append(call)
            ranges.append((start, end))
            cursor = end
        if not ranges:
            return raw, []
        pieces: list[str] = []
        cursor = 0
        for start, end in ranges:
            pieces.append(raw[cursor:start])
            cursor = end
        pieces.append(raw[cursor:])
        cleaned = "".join(pieces)
        cleaned = re.sub(r"</?(?:tool_call|function_call)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(?im)^[ \t]*```(?:json)?[ \t]*$", "", cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned, calls

    @staticmethod
    def _memo_management_instruction_matches(text: Any) -> bool:
        value = str(text or "")
        return bool(
            re.search(
                r"便签|便笺|备忘录?|待办|帮我记(?:一下|下来)?|记(?:一下|下来)|"
                r"(?:确认|确定|取消)(?:删除|删掉|移除)|"
                r"(?:完成|恢复|置顶|取消置顶|删除|删掉).{0,4}(?:第?\s*\d+|这张|那张)|"
                r"第?\s*\d+(?:张|条|个)?.{0,8}(?:完成|恢复|置顶|删除|删掉|改到|改成)|"
                r"(?:只看|查看|看看).{0,4}(?:已完成|进行中|全部)",
                value,
                flags=re.I,
            )
        )

    def _remove_future_task_for_memo_request(self, req: Any, text: Any) -> bool:
        """明确的便签操作只保留便签工具，避免同轮再创建官方定时任务。"""
        if not self._memo_management_instruction_matches(text):
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        has_future_task = False
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            try:
                has_future_task = get_tool("future_task") is not None
            except Exception:
                pass
        tools = getattr(tool_set, "tools", None)
        if not has_future_task and isinstance(tools, list):
            has_future_task = any(
                _single_line(getattr(tool, "name", ""), 120) == "future_task"
                for tool in tools
            )
        if not has_future_task:
            return False
        remove_tool = getattr(tool_set, "remove_tool", None)
        try:
            if callable(remove_tool):
                remove_tool("future_task")
            elif isinstance(tools, list):
                tool_set.tools = [
                    tool
                    for tool in tools
                    if _single_line(getattr(tool, "name", ""), 120) != "future_task"
                ]
            else:
                return False
        except Exception as exc:
            logger.warning("便签请求移除 future_task 失败: %s", _single_line(exc, 160))
            return False
        return True

    @staticmethod
    def _scope_reaction_media_tools_for_request(
        req: Any,
        *,
        explicit_media_request: bool,
        reaction_authorized: bool,
        reaction_evaluated: bool,
        allow_photo_on_reaction_turns: bool = False,
    ) -> list[str]:
        """Keep ordinary experimental replies on the single-pass intent path."""
        if explicit_media_request:
            return []
        # Current-media delivery is only meaningful after an explicit request
        # caused another tool to produce a local image in this same turn.
        blocked = {"pc_send_current_media"}
        # Distinguish "not evaluated" (ordinary passive turn, keep the media
        # tools for regular regeneration/expression use) from "evaluated":
        # an evaluated reaction turn must hide the automatic reaction-media
        # tools regardless of whether it was authorized. An authorized turn
        # switches to the internal response tag, while a denied turn must not
        # fall through the legacy media-tool path. Explicit media requests
        # were already returned above and keep every tool visible.
        if reaction_evaluated:
            # The reaction gallery lookup always stays hidden on evaluated
            # turns: its automatic path is the internal response tag, and
            # leaving both available would produce duplicate images.
            blocked.add("pc_find_reaction_image")
            # By default an evaluated turn keeps the photo tool hidden too.
            # When allow_photo_on_reaction_turns is enabled (opt-in only),
            # the photo tool stays in the declaration so the model can answer
            # colloquial see-photo requests on authorized reaction turns; all
            # quotas, daily caps and content boundaries still run inside the
            # tool itself.
            if not allow_photo_on_reaction_turns:
                blocked.add("pc_generate_photo")
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return []
        tools = getattr(tool_set, "tools", None)
        names = {
            _single_line(getattr(tool, "name", ""), 120)
            for tool in tools
        } if isinstance(tools, list) else set()
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            for name in blocked:
                try:
                    if get_tool(name) is not None:
                        names.add(name)
                except Exception:
                    pass
        remove_tool = getattr(tool_set, "remove_tool", None)
        removed: list[str] = []
        for name in sorted(blocked):
            if name not in names and isinstance(tools, list):
                continue
            try:
                if callable(remove_tool):
                    remove_tool(name)
                elif isinstance(tools, list):
                    tool_set.tools = [
                        tool
                        for tool in tool_set.tools
                        if _single_line(getattr(tool, "name", ""), 120) != name
                    ]
                else:
                    continue
                removed.append(name)
            except Exception as exc:
                logger.debug(
                    "裁剪实验性表情工具失败: tool=%s error=%s",
                    name,
                    _single_line(exc, 160),
                )
        return removed

    @staticmethod
    def _mark_memo_request_tool_boundary(event: AstrMessageEvent, req: Any) -> None:
        try:
            setattr(event, "private_companion_explicit_memo_request", True)
            setattr(event, "_private_companion_memo_provider_request", req)
        except Exception:
            pass

    def _finalize_memo_request_tool_boundary(self, event: AstrMessageEvent) -> bool:
        """在 AstrBot 补齐内置工具后再次执行便签/定时工具互斥。"""
        if not bool(getattr(event, "private_companion_explicit_memo_request", False)):
            return False
        req = getattr(event, "_private_companion_memo_provider_request", None)
        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra):
            try:
                final_req = get_extra("provider_request")
            except Exception:
                final_req = None
            if final_req is not None:
                req = final_req
        if req is None:
            return False
        return self._remove_future_task_for_memo_request(
            req,
            getattr(event, "message_str", ""),
        )

    def _memo_management_tool_prompt_section(self) -> PromptSection:
        body = """主要用户在私聊里要求新增、查看、修改、完成、恢复、置顶或删除便签时，使用 `pc_manage_memo`，不要只用口头承诺代替实际操作。
- 只有用户明确说“便签/便笺/备忘/待办/帮我记一下/记下来”或正在继续操作已有便签时，才把请求路由到本工具。普通“提醒我/叫醒我/定时/半小时后通知我/别忘了”属于临时提醒，不要擅自建成便签。
- 新增：action=create，title/content 至少传一项；提醒时间传 due_at，可传 `2026-07-15 09:00`，也支持“明早9点”“两小时后”“周五下午3点”等常见表达。
- 查看：action=list；默认 status=active，可用 status=completed/all 查看已完成或全部便签，query 可按标题/正文筛选。列表正文只是预览，需要完整正文时用 action=get + selector。后续用编号操作时要传回相同 status，优先使用返回的 id。
- 修改/完成/恢复/置顶：action=update/complete/reopen/pin/unpin，并用 selector 传便签标题、编号或工具返回的 id。匹配到多张时必须让用户进一步指定，不能自行选择。
- 删除：首次 action=delete 只会返回 confirmation_required，必须让用户回复“确认删除”或“取消删除”；确认时把 confirmation_token 原样传给下一次 delete，取消时 action=cancel_delete。不能绕过确认。
- 含 due_at 且开启提醒的便签，其提醒已经由便签自身负责；成功保存后不得再调用 `future_task`，也不得再输出 `<timer>`，否则会重复提醒。
- 只有工具明确返回 `saved=true`，才能说便签已经新增、修改、完成、恢复、置顶或删除；cancel_delete 返回 `cancelled=true` 时才能说已取消删除。其他 `saved=false`、失败、歧义或等待确认必须如实说明。
- 便签是待办，不是已经发生的经历；不要把未完成事项说成用户已经做过。
""".strip()
        return prompt_section(
            key="tools.memo_management",
            title="备忘便签工具",
            source="tools",
            content=body,
        )

    def _memo_management_tool_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._memo_management_tool_prompt_section()
        )

    @staticmethod
    def _schedule_management_instruction_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 240))
        if not compact:
            return False
        operation = bool(re.search(r"(重置|重做|重新细化|重新生成|刷新|取消|删除|删掉|移除|去掉)", compact))
        target = bool(
            re.search(r"(日程|行程|安排|计划|时段|时间段|这段|那段|第[一二两三四五六七八九十\d]+段)", compact)
            or re.search(r"(?:凌晨|早上|上午|中午|下午|傍晚|晚上|今晚)?(?:\d{1,2}|[一二两三四五六七八九十]+)(?:点|时|:|：).{0,10}(?:那段|的安排|的计划)", compact)
        )
        return bool(operation and target)

    def _schedule_management_tool_prompt_section(self) -> PromptSection:
        body = """主要用户在私聊中明确要求重置、重做、重新细化、取消或删除某一段今日日程时，使用 `pc_manage_schedule`，不要只口头承诺。
- 重新细化：action=regenerate；取消/删除/移除：action=cancel。“删除”采用取消语义，保留历史依据，但不会再作为当前活动、细化重试或主动消息契机。
- selector 必须保留用户明确给出的时间、序号或活动关键词，例如“下午三点”“第二段”“整理房间”；不要自行猜一个日程段。工具返回歧义或未命中时，把候选自然列给用户继续选择。
- 只有用户明确要求操作已有日程时才调用。普通聊天中的“我下午出门”“今晚想晚点睡”“你可以休息”等生活信息仍按对话和柔性日程调整理解，不得擅自取消或重置日程。
- 只有工具返回 `saved=true` 才能说操作已经完成；失败、歧义或未找到时必须如实说明。
""".strip()
        return prompt_section(
            key="tools.schedule_management",
            title="指定日程管理工具",
            source="tools",
            content=body,
        )

    def _schedule_management_tool_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._schedule_management_tool_prompt_section()
        )

    def _memo_tool_authorization(self, event: AstrMessageEvent) -> tuple[bool, str]:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        allowed = bool(is_private and requester_id and self._is_private_companion_owner_user_id(requester_id))
        if not allowed:
            logger.info(
                "便签管理权限未通过: private=%s sender=%s umo=%s",
                is_private,
                requester_id or "-",
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed, requester_id
