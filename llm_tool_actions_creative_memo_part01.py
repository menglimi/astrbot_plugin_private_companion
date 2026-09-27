# -*- coding: utf-8 -*-
"""LlmToolActionsCreativeMemoPart01Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_creative_memo.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 453 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsCreativeMemoMixin）。
"""
from __future__ import annotations

import json
import re
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _safe_int, _single_line
from .llm_tool_actions_shared import _render_tool_prompt_section_labeled, logger
from .memo_notes import normalize_memo_note
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsCreativeMemoPart01Mixin:
    """LlmToolActionsCreativeMemoPart01Mixin（从 LlmToolActionsCreativeMemoMixin 拆出）。"""


    def _creative_work_tool_prompt_section(self) -> PromptSection | None:
        if not self.enabled or not runtime_persona_setting(self, 'enable_creative_work_read_guard', True):
            return None
        body = """当用户询问能否看到资料柜/书架、资料柜是否为空、里面有什么或有几篇作品时，必须先调用 `pc_view_creative_work`，action=list。list 返回的是插件当前真实保存的资料柜库存；主要用户还会得到日记、资料归档和便签的分类数量。
当用户询问你自己的某篇创作写了什么、某一部分/片段的内容、你如何看待这篇创作、为什么这样写，或要求你结合原文讲讲时，必须先调用 `pc_view_creative_work` 读取真实创作，再依据工具结果回答。
- 按标题读取：action=get，selector 传用户提到的作品标题；只有用户明确指定“第 N 部分/第 N 段”时才传 part=N。
- 不确定有哪些作品或用户泛问“最近写了什么”：先 action=list；拿到准确标题后，如需正文再 action=get。
- 讨论整篇作品时 part=0，工具会按顺序返回预算内的正文；结果若 truncated=true，可继续用 next_part 读取。
- 工具返回 success 前，不要说“我看过了/我刚检查了”；也不要先发送“我先去看看”等准备动作。直接调用工具，取得结果后一次性自然回答。
- 回复必须直接说读取结果，不要用“（翻了翻资料柜）”“（挠挠头）”之类括号动作代替结果。
- 不得把被动提示中的短片段、长期记忆或聊天印象冒充完整原文；找不到作品或部分时如实说明，并可根据 candidates 请用户进一步说明。
- 这是只读工具，不能修改、续写或删除创作。
- 用户只是让你讲一个、编一个或说一个新故事，或泛泛地让你讲“你的故事”时，不是在读取资料柜作品，不要调用此工具；只有用户明确提到你写过的故事、某篇作品、资料柜内容、原文或具体章节时才读取。
- 用户要求查看配置文件、数据文件、日志、源码、代码、脚本、插件目录或配置项时，不是在读取资料柜作品；即使文件或配置名称中包含“创作”“作品”等词，也不要调用此工具，不要把技术文件问答改写成创作原文读取失败。
""".strip()
        return prompt_section(
            key="tools.creative_work",
            title="资料柜与自己的创作读取工具",
            source="tools",
            content=body,
        )

    def _creative_work_tool_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._creative_work_tool_prompt_section()
        )

    @staticmethod
    def _creative_work_inventory_query_matches(text: Any) -> bool:
        normalized = _single_line(text, 260)
        if not normalized or any(
            token in normalized
            for token in ("资料柜密码", "书架密码", "夹层密码", "抽屉密码", "输出密码", "重置密码")
        ):
            return False
        shelf_terms = ("资料柜", "书架", "作品柜", "创作柜")
        query_terms = (
            "能看到", "看得到", "能看见", "可以看到", "能不能看", "能读到",
            "看看", "看一下", "查一下", "查查", "查询", "检索", "列一下", "列出",
            "里面有什么", "有什么", "有哪些",
            "有几", "多少", "空不空", "是不是空", "还是空", "空的", "现在有",
        )
        return any(token in normalized for token in shelf_terms) and any(
            token in normalized for token in query_terms
        )

    def _creative_work_query_instruction_matches(self, text: Any) -> bool:
        normalized = _single_line(text, 260)
        if not normalized:
            return False
        technical_file_terms = (
            "配置文件", "数据文件", "日志文件", "代码文件", "项目文件", "插件文件",
            "配置项", "配置键", "配置目录", "插件目录", "文件目录", "文件夹",
            "源码", "源代码", "代码", "脚本", "仓库", "数据库", "报错日志",
        )
        technical_extensions = re.search(
            r"(?:^|[\\/\s])[^\\/\s]{1,100}\.(?:json|ya?ml|toml|ini|cfg|conf|env|py|js|ts|tsx|jsx|md|txt|log|db|sqlite3?)\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if any(token in normalized for token in technical_file_terms) or technical_extensions:
            return False
        if self._creative_work_inventory_query_matches(normalized):
            return True

        # “故事”也常用于临时讲述或现场创作。只有句子同时指向一篇已经
        # 存在的作品时，才把它当作资料柜读取请求。
        if "故事" in normalized:
            existing_story_anchors = (
                "你写的", "你写过的", "你以前写的", "你之前写的", "你最近写的",
                "你创作的", "你创作过的", "自己写的", "自己创作的",
                "那篇", "这篇", "哪篇", "那部", "这部", "哪部",
                "那篇故事", "这篇故事", "哪篇故事", "那个故事", "这个故事",
                "上次的故事", "之前的故事", "资料柜里的故事", "书架里的故事",
                "故事原文", "故事正文", "故事全文", "故事片段", "故事章节",
                "故事的原文", "故事的正文", "故事的全文", "故事的片段", "故事的章节",
                "故事第", "故事写了什么", "故事写的什么", "写过什么故事",
                "写了什么故事", "创作过什么故事", "创作了什么故事",
            )
            has_existing_story_anchor = any(
                token in normalized for token in existing_story_anchors
            ) or bool(
                re.search(r"《[^》]{1,80}》", normalized)
                or re.search(r"故事.{0,12}第\s*[一二三四五六七八九十百零两\d]+\s*(?:部分|章|节|段)", normalized)
            )
            if not has_existing_story_anchor:
                return False
        work_terms = (
            "创作", "作品", "写作", "札记", "随笔", "散文", "小说", "故事",
            "诗", "歌词", "剧本", "手稿", "草稿", "正文", "片段", "章节",
        )
        query_terms = (
            "讲讲", "说说", "看看", "看一下", "读", "回顾", "总结", "内容",
            "写了什么", "写过什么", "写的什么", "创作过什么",
            "怎么看", "看待", "觉得", "想法", "为什么",
            "第", "部分", "哪一段", "这一段", "那一段", "原文", "全文",
        )
        return any(token in normalized for token in work_terms) and any(
            token in normalized for token in query_terms
        )

    @staticmethod
    def _creative_work_tool_result_payload(tool_result: Any) -> dict[str, Any]:
        """Extract the plugin JSON from AstrBot's CallToolResult wrapper."""
        pending: list[Any] = [tool_result]
        seen: set[int] = set()
        while pending and len(seen) < 24:
            value = pending.pop(0)
            if value is None:
                continue
            marker = id(value)
            if marker in seen:
                continue
            seen.add(marker)
            if isinstance(value, dict):
                if "status" in value:
                    return dict(value)
                for key in (
                    "structuredContent", "structured_content", "result", "data", "content", "text",
                ):
                    if key in value:
                        pending.append(value.get(key))
                continue
            if isinstance(value, (list, tuple)):
                pending.extend(value)
                continue
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    if "status" in parsed:
                        return parsed
                    pending.append(parsed)
                continue
            for attr in (
                "structuredContent", "structured_content", "result", "data", "content", "text",
            ):
                try:
                    nested = getattr(value, attr, None)
                except Exception:
                    nested = None
                if nested is not None:
                    pending.append(nested)
        return {}

    @staticmethod
    def _record_creative_work_tool_result(
        event: AstrMessageEvent,
        tool: Any,
        tool_args: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "pc_view_creative_work":
            return False
        try:
            setattr(event, "private_companion_creative_work_tool_attempted", True)
            action = _single_line(
                (tool_args or {}).get("action") if isinstance(tool_args, dict) else "",
                20,
            ).lower() or "get"
            payload = LlmToolActionsCreativeMemoPart01Mixin._creative_work_tool_result_payload(tool_result)
            success = bool(
                action in {"list", "get"}
                and _single_line(payload.get("status"), 24).lower() == "success"
                and not bool(getattr(tool_result, "isError", False))
            )
            setattr(event, "private_companion_creative_work_read_success", success)
            setattr(event, "private_companion_creative_work_tool_action", action)
            setattr(event, "private_companion_creative_work_tool_status", _single_line(payload.get("status"), 24))
            setattr(
                event,
                "private_companion_bookshelf_inventory_complete",
                bool(action == "list" and isinstance(payload.get("bookshelf"), dict)),
            )
        except Exception:
            pass
        return True

    @staticmethod
    def _strip_bookshelf_stage_directions(text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        action_terms = (
            "查", "看", "翻", "找", "确认", "检查", "扫", "数", "挠头", "挠挠头",
            "点头", "摇头", "眨眼", "歪头", "低头", "抬头", "叹气", "笑", "脸红",
            "不好意思", "认真", "仔细", "凑近", "摊手", "耸肩",
        )
        pattern = re.compile(r"(?:^|\n)\s*[（(]([^（）()\n]{1,80})[）)]\s*")

        def replace(match: re.Match[str]) -> str:
            content = match.group(1)
            return "\n" if any(token in content for token in action_terms) else match.group(0)

        cleaned = pattern.sub(replace, raw)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    @staticmethod
    def _bookshelf_requester_is_owner(event: AstrMessageEvent, plugin: Any) -> bool:
        try:
            requester = event.get_sender_id()
        except Exception:
            requester = ""
        identity_for_event = getattr(plugin, "_event_permission_identity_id", None)
        identity = getattr(plugin, "_permission_identity_id", None)
        if callable(identity_for_event):
            try:
                requester = identity_for_event(event)
            except Exception:
                requester = ""
        elif callable(identity):
            try:
                requester = identity(requester)
            except Exception:
                requester = ""
        checker = getattr(plugin, "_is_private_companion_owner_user_id", None)
        if not requester or not callable(checker):
            return False
        try:
            return bool(checker(requester))
        except Exception:
            return False

    def _bookshelf_inventory_snapshot(
        self,
        event: AstrMessageEvent,
        projects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        source_projects = projects
        if source_projects is None:
            raw_projects = self.data.get("creative_projects") if isinstance(getattr(self, "data", None), dict) else []
            source_projects = list(raw_projects) if isinstance(raw_projects, list) else []
        eligible = self._creative_work_project_candidates(source_projects, "")
        snapshot: dict[str, Any] = {
            "scope": "public",
            "creative_count": len(eligible),
            "creative_projects": [
                self._creative_work_project_summary(project, index)
                for index, project in enumerate(eligible[-20:], start=max(1, len(eligible) - 19))
            ],
        }
        if not self._bookshelf_requester_is_owner(event, self):
            return snapshot

        raw_diaries = self.data.get("bot_diaries") if isinstance(self.data.get("bot_diaries"), list) else []
        diaries = [item for item in raw_diaries if isinstance(item, dict)]
        raw_shelf_items = self.data.get("bookshelf_items") if isinstance(self.data.get("bookshelf_items"), list) else []
        reading_items: list[dict[str, Any]] = []
        raw_notes = self.data.get("memo_notes") if isinstance(self.data.get("memo_notes"), list) else []
        notes = [note for note in (normalize_memo_note(item) for item in raw_notes) if note]
        snapshot.update(
            {
                "scope": "owner",
                "diary_count": len(diaries),
                "reading_archive_count": len(reading_items),
                "reading_archive_titles": [
                    _single_line(item.get("title"), 80) or "未命名阅读记录"
                    for item in reading_items[-8:]
                ],
                "memo_active_count": sum(1 for note in notes if note.get("status") == "active"),
                "memo_completed_count": sum(1 for note in notes if note.get("status") == "completed"),
            }
        )
        return snapshot

    def _format_bookshelf_inventory_reply(self, event: AstrMessageEvent) -> str:
        snapshot = self._bookshelf_inventory_snapshot(event)
        creative_projects = snapshot.get("creative_projects") if isinstance(snapshot.get("creative_projects"), list) else []
        titles = [
            _single_line(item.get("title"), 60)
            for item in creative_projects[-5:]
            if isinstance(item, dict) and _single_line(item.get("title"), 60)
        ]
        creative_count = _safe_int(snapshot.get("creative_count"), 0, 0)
        sections: list[str] = []
        if creative_count:
            title_text = f"，最近的是{'、'.join(f'《{title}》' for title in titles)}" if titles else ""
            sections.append(f"创作区有 {creative_count} 篇带正文的作品{title_text}")
        else:
            sections.append("创作区暂时没有带正文的作品")
        if snapshot.get("scope") == "owner":
            sections.extend(
                (
                    f"日记本有 {_safe_int(snapshot.get('diary_count'), 0, 0)} 天记录",
                    f"资料归档有 {_safe_int(snapshot.get('reading_archive_count'), 0, 0)} 条记录",
                    f"便签区有 {_safe_int(snapshot.get('memo_active_count'), 0, 0)} 张进行中便签",
                )
            )
        return "能看到。现在" + "；".join(sections) + "。"

    def _bookshelf_reply_conflicts_with_inventory(self, event: AstrMessageEvent, text: Any) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return True
        snapshot = self._bookshelf_inventory_snapshot(event)
        visible_count = _safe_int(snapshot.get("creative_count"), 0, 0)
        if snapshot.get("scope") == "owner":
            visible_count += _safe_int(snapshot.get("diary_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("reading_archive_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("memo_active_count"), 0, 0)
            visible_count += _safe_int(snapshot.get("memo_completed_count"), 0, 0)
        claims_empty = bool(
            re.search(
                r"(?:资料柜|书架)?[^。！？!?\n]{0,12}(?:还是|仍然|依旧|目前|现在)?"
                r"(?:空空的|是空的|空着|什么都没有|没有东西|没东西|没有内容)",
                cleaned,
            )
        )
        return visible_count > 0 and claims_empty

    def _guard_unread_creative_work_response(self, event: AstrMessageEvent, text: Any) -> str:
        raw = str(text or "")
        if not runtime_persona_setting(self, 'enable_creative_work_read_guard', True):
            return raw
        if not bool(getattr(event, "private_companion_creative_work_tool_required", False)):
            return raw
        inbound_text = str(getattr(event, "message_str", "") or "")
        inventory_query = self._creative_work_inventory_query_matches(inbound_text)
        cleaned = self._strip_bookshelf_stage_directions(raw) if inventory_query else raw.strip()
        read_success = bool(getattr(event, "private_companion_creative_work_read_success", False))
        inventory_complete = bool(getattr(event, "private_companion_bookshelf_inventory_complete", False))
        if read_success and cleaned and not (
            inventory_query
            and (
                not inventory_complete
                or self._bookshelf_reply_conflicts_with_inventory(event, cleaned)
            )
        ):
            return cleaned
        if inventory_query:
            logger.warning(
                "资料柜查询未形成可信正文，已按本地真实库存回答: attempted=%s status=%s inventory_complete=%s session=%s",
                bool(getattr(event, "private_companion_creative_work_tool_attempted", False)),
                _single_line(getattr(event, "private_companion_creative_work_tool_status", ""), 24) or "none",
                inventory_complete,
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return self._format_bookshelf_inventory_reply(event)
        if bool(getattr(event, "private_companion_creative_work_tool_attempted", False)):
            return "我这次没能实际读取到对应的创作原文，先不凭印象乱讲。你可以再告诉我准确标题或第几部分，我读到后再认真和你说。"
        logger.warning(
            "指定创作问答未实际调用读取工具，已阻止凭片段作答: session=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        return "我这次还没能实际读取到对应的创作原文，先不凭印象乱讲。你可以再告诉我准确标题或第几部分，我读到后再认真和你说。"

    @staticmethod
    def _plaintext_tool_call_from_object(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        function = value.get("function")
        source = function if isinstance(function, dict) else value
        name = _single_line(source.get("name") or value.get("tool_name"), 80)
        # TODO: derive this allowlist from the @filter.llm_tool registrations in
        # main.py once AstrBot exposes a stable registry during decoration.
        known_names = {
            "pc_qzone_view_feed",
            "pc_qzone_publish_feed",
            "pc_generate_photo",
            "pc_send_current_media",
            "pc_find_reaction_image",
            "pc_manage_memo",
            "pc_manage_schedule",
            "pc_view_creative_work",
            "pc_get_group_id_by_name",
            "pc_get_user_id_by_name",
            "pc_query_relation_person",
            "pc_get_specified_group_members",
            "pc_query_wardrobe_detail",
            "pc_set_outfit_intent",
            "pc_query_interaction",
            "pc_relay_message",
            "pc_send_to_group",
            "pc_send_to_private_user",
            "pc_send_to_groups",
            "pc_send_to_private_users",
            "pc_schedule_group_relay",
            "future_task",
            "send_message_to_user",
        }
        if name not in known_names:
            return None
        parameters = source.get("parameters")
        if parameters is None:
            parameters = source.get("arguments")
        if parameters is None:
            parameters = source.get("args")
        if parameters is None:
            parameters = value.get("parameters", value.get("arguments", value.get("args", {})))
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except Exception:
                return None
        if not isinstance(parameters, dict):
            return None
        return {"name": name, "parameters": dict(parameters)}

    @staticmethod
    def _creative_work_project_candidates(
        projects: list[dict[str, Any]],
        selector: Any,
    ) -> list[dict[str, Any]]:
        eligible = [
            item
            for item in projects
            if isinstance(item, dict)
            and str(item.get("status") or "") in {"drafting", "finished"}
            and isinstance(item.get("draft_chunks"), list)
            and any(
                isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
                for chunk in item.get("draft_chunks", [])
            )
        ]
        value = _single_line(selector, 120)
        if not value:
            return eligible
        folded = value.casefold()
        exact = [
            item
            for item in eligible
            if folded
            in {
                _single_line(item.get("id"), 40).casefold(),
                _single_line(item.get("title"), 80).casefold(),
            }
        ]
        if exact:
            return exact
        contains = [
            item
            for item in eligible
            if folded in _single_line(item.get("title"), 80).casefold()
            or _single_line(item.get("title"), 80).casefold() in folded
        ]
        if contains:
            return contains
        number_match = re.fullmatch(r"(?:第\s*)?(\d+)(?:\s*(?:个|篇|项))?", value)
        if number_match:
            index = _safe_int(number_match.group(1), 0) - 1
            if 0 <= index < len(eligible):
                return [eligible[index]]
        return []

    @staticmethod
    def _creative_work_project_summary(project: dict[str, Any], index: int = 0) -> dict[str, Any]:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        valid_chunks = [
            chunk
            for chunk in chunks
            if isinstance(chunk, dict) and str(chunk.get("text") or "").strip()
        ]
        return {
            "index": index,
            "id": _single_line(project.get("id"), 40),
            "title": _single_line(project.get("title"), 80) or "未定标题",
            "work_type": _single_line(project.get("work_type"), 40) or "文本作品",
            "status": _single_line(project.get("status"), 24),
            "part_count": len(valid_chunks),
            "current_chars": _safe_int(project.get("current_chars"), 0, 0),
        }
