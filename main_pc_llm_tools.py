# -*- coding: utf-8 -*-
"""pc_llm_tools。

由 tools/split_main_domain.py 从 main.py 机械抽取（13 个方法 / 477 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import json
import re
from .helpers import _single_line
from .main_shared import _multi_persona_event_context
from .persona_config import runtime_persona_setting
from .planning import generate_detail_enhancement
from astrbot.api.event import AstrMessageEvent, filter


class PrivateCompanionPluginPcLlmToolsMixin:
    """pc_llm_tools（从 PrivateCompanionPlugin 拆出）。"""

    @filter.llm_tool(name="pc_qzone_view_feed")
    @_multi_persona_event_context
    async def pc_qzone_view_feed(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        target_scope: str = "",
        target_uin: str = "",
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
        selector: str = "",
        fid: str = "",
        time_hint: str = "",
        **kwargs,
    ) -> str:
        """查看指定归属的 QQ 空间说说,可按需点赞或评论。

        Args:
            user_id(string): 兼容旧调用的明确 QQ 号；不能再作为省略目标时的默认值。
            target_scope(string): 可选归属：bot_self（兼容 self）、current_user 或 explicit_uin；用户原话已有明确“你/我”归属时工具也会语义校正，确实含糊时返回 needs_target。
            target_uin(string): target_scope=explicit_uin 时要查看的 QQ 号；可用 user_id 兼容旧调用。
            pos(number): 可选,说说位置,0 表示最新一条。
            like(boolean): 可选,是否给该条说说点赞。
            reply(boolean): 可选,是否按工具内部规则尝试评论。
            selector(string): 可选,自然语言选择器,如“最新”“第2条”“最后”；也可以填 fid。
            fid(string): 可选,明确指定说说 fid。
            time_hint(string): 可选,发布时间提示,如“今天下午6点多”或“18:20”。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_qzone_view_feed_impl(
            event,
            user_id=user_id,
            target_scope=target_scope,
            target_uin=target_uin,
            pos=pos,
            like=like,
            reply=reply,
            selector=selector,
            fid=fid,
            time_hint=time_hint,
            **kwargs,
        )

    @filter.llm_tool(name="pc_qzone_publish_feed")
    @_multi_persona_event_context
    async def pc_qzone_publish_feed(
        self,
        event: AstrMessageEvent,
        text: str = "",
        images: list[str] | None = None,
        image: str = "",
        image_path: str = "",
        image_url: str = "",
        use_latest_draft: bool = False,
        **kwargs,
    ) -> str:
        """发布一条 QQ 空间说说。必须通过 text 参数传入最终正文；如需带图,通过 images 或 image 传入图片。

        Args:
            text(string): 要发布到 QQ 空间的说说正文。
            images(list[string]): 可选,要随说说发布的本地图片路径或图片 URL 列表。
            image(string): 可选,单张图片的本地路径或图片 URL。
            image_path(string): 可选,单张本地图片路径。
            image_url(string): 可选,单张图片 URL。
            use_latest_draft(boolean): 可选,是否使用最近生成的生活说说草稿。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        if images:
            kwargs["images"] = images
        if image:
            kwargs["image"] = image
        if image_path:
            kwargs["image_path"] = image_path
        if image_url:
            kwargs["image_url"] = image_url
        if use_latest_draft:
            kwargs["use_latest_draft"] = use_latest_draft
        return await self._pc_qzone_publish_feed_impl(event, text, **kwargs)

    @filter.llm_tool(name="pc_qzone_reply_my_comment")
    @_multi_persona_event_context
    async def pc_qzone_reply_my_comment(
        self,
        event: AstrMessageEvent,
        comment_hint: str = "",
        selector: str = "latest",
        reply_hint: str = "",
    ) -> str:
        """检查并回复用户刚在 Bot 自己 QQ 空间动态下留下的评论。

        Args:
            comment_hint(string): 用户记得的评论全文、关键词或大致说法；无法精确映射 QQ 身份时用于唯一匹配。
            selector(string): 可选，优先检查“最新”动态。
            reply_hint(string): 可选，用户希望 Bot 使用的公开回复语气或内容提示。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"当前模式不可使用 QQ 空间工具。"}'
        try:
            result = await self._qzone_reply_my_comment(
                event,
                comment_hint=comment_hint,
                selector=selector,
                reply_hint=reply_hint,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": _single_line(exc, 160)}, ensure_ascii=False)

    @filter.llm_tool(name="pc_generate_photo")
    @_multi_persona_event_context
    async def pc_generate_photo(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        kind: str = "text2img",
        reference_image_path: str = "",
        image_size: str = "",
        send: bool = True,
        caption: str = "",
        scene_preset: str = "",
        **kwargs,
    ) -> str:
        """调用 Private Companion 生图/自拍/改图能力。

        Args:
            prompt(string): 画面描述、自拍要求或改图要求。若用户在前几轮文字剧情中已经明确换装，而本轮只说继续/再拍一张，必须把仍生效的具体服装展开写进 prompt（例如“角色当前仍穿 JK 校服，继续拍摄”），不能只写“继续”让下游猜测。
            kind(string): text2img/selfie/sticker/edit。角色本人以自拍、背影、侧脸或环境人像等任何形式出镜时用 selfie；角色表情包/贴纸用 sticker；不含角色本人的普通场景、物件、风景用 text2img；改图用 edit。
            reference_image_path(string): 可选，本地图片路径或图片 URL；edit 必填，selfie 可留空自动使用人设参考图/当天基础穿搭图。本轮引用图片会由工具自动解析，无需猜测路径；没有引用图片时不会自动复用上一张成图。合影需要本轮用户消息/引用消息中的其他人物参考图，或在 prompt 中明确点名已绑定可用参考图的关系网角色；后者由工具自动选图，单独填写或猜测此参数不能授权合影。当天基础穿搭只是默认基线，近期对话已发生的换装优先。
            image_size(string): 可选，在线图片 API 尺寸，如 1024x1024。
            send(boolean): 是否生成后直接发送到当前会话，默认 true。
            caption(string): 发送图片时附带的短文字。
            scene_preset(string): 可选场景预设建议，如 角色自拍/COS自拍/日常穿搭/镜前穿搭/头像特写/房间日常/可拍画面/表情包场景；不会覆盖用户原话或参考图强约束。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        inbound_text = str(getattr(event, "message_str", "") or "")
        reaction_kind = _single_line(kind, 24).casefold() in {
            "sticker",
            "emoji",
            "meme",
            "表情包",
            "贴纸",
        } or bool(re.search(r"(?:表情包|反应图|贴纸|梗图|斗图)", str(prompt or ""), flags=re.I))
        if (
            reaction_kind
            and self._reaction_expression_opt_out_requested(inbound_text)
            and not self._photo_generation_instruction_matches(inbound_text)
        ):
            return json.dumps(
                {
                    "status": "skipped",
                    "success": True,
                    "generated": False,
                    "sent": False,
                    "skip_reason": "explicit_opt_out",
                    "message": "用户本轮明确要求不发表情包",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "尊重用户边界，只继续自然文字回复。",
                },
                ensure_ascii=False,
            )
        reaction_authorization = self._reaction_expression_authorization(event)
        allow_photo_on_reaction_turns = bool(
            runtime_persona_setting(
                self,
                'allow_generate_photo_on_reaction_turns',
                False,
            )
        )
        if (
            reaction_authorization
            and not allow_photo_on_reaction_turns
            and not self._photo_generation_instruction_matches(
                getattr(event, "message_str", "")
            )
        ):
            return json.dumps(
                {
                    "status": "skipped",
                    "success": True,
                    "generated": False,
                    "sent": False,
                    "message": "本轮没有显式生图请求，继续自然文字回复即可。",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "不要解释内部工具边界，按原语境继续自然文字回复。",
                },
                ensure_ascii=False,
            )
        return await self._pc_generate_photo_impl(
            event,
            prompt=prompt,
            kind=kind,
            reference_image_path=reference_image_path,
            image_size=image_size,
            send=send,
            caption=caption,
            scene_preset=scene_preset,
            **kwargs,
        )

    @filter.llm_tool(name="pc_send_current_media")
    @_multi_persona_event_context
    async def pc_send_current_media(
        self,
        event: AstrMessageEvent,
        media_path: str = "",
        caption: str = "",
        destination: str = "current",
        **kwargs,
    ) -> str:
        """发送本轮或紧邻上一轮其他工具刚生成、但尚未投递的本地图片。

        Args:
            media_path(string): 最近一次工具明确返回的本地图片路径；仅支持 AstrBot 临时目录或本插件成图目录，不得猜测路径。
            caption(string): 可选，随图片发送的一句自然短文；不要填写发送状态回执。
            destination(string): current（默认，发到当前会话）或 requester_private（仅在当前请求者明确说“私聊发我”等要求时，私聊发给请求者本人）；不能指定第三方。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_generate_photo"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用当前媒体投递工具。"}'
        return await self._pc_send_current_media_impl(
            event,
            media_path=media_path,
            caption=caption,
            destination=destination,
            **kwargs,
        )

    @filter.llm_tool(name="pc_manage_memo")
    @_multi_persona_event_context
    async def pc_manage_memo(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        title: str = "",
        content: str = "",
        selector: str = "",
        due_at: str = "",
        repeat: str = "",
        color: str = "",
        remind_enabled: bool | None = None,
        include_completed: bool = False,
        status: str = "",
        query: str = "",
        clear_due: bool = False,
        clear_content: bool = False,
        confirmation_token: str = "",
    ) -> str:
        """在主要用户私聊中新增、查看、修改、完成、恢复、置顶或删除备忘便签。

        Args:
            action(string): list/get/create/update/complete/reopen/delete/cancel_delete/pin/unpin。
            title(string): 新增时的标题，或修改后的标题。
            content(string): 新增时的正文，或修改后的正文。
            selector(string): 要操作的便签标题、列表编号或便签 id。
            due_at(string): 可选提醒时间，可传绝对日期或“明早9点”“两小时后”等常见表达。
            repeat(string): 可选，none/daily/weekly/monthly/yearly。
            color(string): 可选，yellow/blue/green/rose/gray。
            remind_enabled(boolean): 可选，是否在到期时提醒。
            include_completed(boolean): list 时是否包含已完成便签。
            status(string): 可选，active/completed/all；用于筛选列表或限定编号所在视图。
            query(string): list 时可选，按标题或正文关键词筛选。
            clear_due(boolean): update 时是否清除到期时间和重复设置。
            clear_content(boolean): update 时是否清空正文。
            confirmation_token(string): 删除确认时原样传回首次 delete 返回的令牌。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","saved":false,"message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_manage_memo_impl(
            event,
            action=action,
            title=title,
            content=content,
            selector=selector,
            due_at=due_at,
            repeat=repeat,
            color=color,
            remind_enabled=remind_enabled,
            include_completed=include_completed,
            status=status,
            query=query,
            clear_due=clear_due,
            clear_content=clear_content,
            confirmation_token=confirmation_token,
        )

    @filter.llm_tool(name="pc_manage_schedule")
    @_multi_persona_event_context
    async def pc_manage_schedule(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        selector: str = "",
    ) -> str:
        """按时间、序号或活动名查看、重新细化或取消主要用户的今日日程段。

        Args:
            action(string): list/regenerate/cancel。用户说删除、删掉、移除时使用 cancel。
            selector(string): 用户指定的时间、序号或活动关键词，例如“下午三点”“第二段”“整理房间”。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return json.dumps(
                {"status": "disabled", "saved": False, "message": "主动消息专用模式下不可管理日程。"},
                ensure_ascii=False,
            )
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private or not self._can_manage_private_companion(event):
            return json.dumps(
                {"status": "forbidden", "saved": False, "message": "只有主要用户可以在私聊中管理今日日程。"},
                ensure_ascii=False,
            )
        normalized_action = _single_line(action, 24).lower()
        normalized_action = {
            "delete": "cancel",
            "remove": "cancel",
            "删除": "cancel",
            "取消": "cancel",
            "移除": "cancel",
            "reset": "regenerate",
            "redo": "regenerate",
            "重置": "regenerate",
            "重做": "regenerate",
            "重新细化": "regenerate",
        }.get(normalized_action, normalized_action or "list")
        if normalized_action == "list":
            async with self._data_lock:
                plan = self.data.get("daily_plan", {})
                segments = self._collect_detail_segments(
                    plan if isinstance(plan, dict) else {},
                    {},
                    include_cancelled=True,
                )
                labels = [self._schedule_segment_label(segment) for segment in segments]
            return json.dumps(
                {
                    "status": "success",
                    "saved": False,
                    "action": "list",
                    "segments": labels,
                    "message": "\n".join(labels) if labels else "今天还没有可操作的日程。",
                },
                ensure_ascii=False,
            )
        if normalized_action == "cancel":
            ok, message = await self._cancel_daily_plan_segment_by_selector(selector)
            return json.dumps(
                {"status": "success" if ok else "error", "saved": ok, "action": "cancel", "message": message},
                ensure_ascii=False,
            )
        if normalized_action == "regenerate":
            ok, message, detail = await self._regenerate_daily_plan_segment_by_selector(
                selector,
                generate_detail_enhancement,
            )
            return json.dumps(
                {
                    "status": "success" if ok else "error",
                    "saved": ok,
                    "action": "regenerate",
                    "message": message,
                    "summary": _single_line(detail.get("summary"), 140) if isinstance(detail, dict) else "",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"status": "error", "saved": False, "message": "不支持的日程操作，请使用 list/regenerate/cancel。"},
            ensure_ascii=False,
        )

    @filter.llm_tool(name="pc_view_creative_work")
    @_multi_persona_event_context
    async def pc_view_creative_work(
        self,
        event: AstrMessageEvent,
        action: str = "get",
        selector: str = "",
        part: int = 0,
        max_chars: int = 6000,
    ) -> str:
        """只读查看 Bot 自己资料柜的真实库存、创作项目与正文。

        Args:
            action(string): list/get。list 列出资料柜库存与作品；get 读取指定作品正文。
            selector(string): 作品准确标题、项目 id 或列表编号；留空时 get 默认读取最近一篇。
            part(number): 可选，明确读取第几部分，按 1 开始；0 表示从第一部分起按预算读取。
            max_chars(number): 可选，本次最多返回正文字符数，默认 6000。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_view_creative_work_impl(
            event,
            action=action,
            selector=selector,
            part=part,
            max_chars=max_chars,
        )

    @filter.llm_tool(name="pc_query_relation_person")
    @_multi_persona_event_context
    async def pc_query_relation_person(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询关系网里是否认识某个 QQ、昵称或别名。

        Args:
            keyword(string): QQ 号、昵称、别名，或用户原话里最像名字的部分。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_query_relation_person_impl(event, **kwargs)

    @filter.llm_tool(name="pc_get_specified_group_members")
    @_multi_persona_event_context
    async def pc_get_specified_group_members(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询指定群成员,并标记是否已在关系网中登记。

        Args:
            group_id(string): 目标群号。
            keyword(string): 可选筛选关键词、昵称、群名片或 QQ。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_specified_group_members_impl(event, **kwargs)

    @filter.llm_tool(name="pc_query_wardrobe_detail")
    @_multi_persona_event_context
    async def pc_query_wardrobe_detail(
        self, event: AstrMessageEvent, scope: str = "today", slot: str = "", **kwargs
    ) -> str:
        """查看角色衣柜的更多细节：某个部位都有什么、今天这身每件是什么、整份衣柜清单。

        只在用户或剧情需要具体衣物时调用一次即可；衣柜为空或未启用时工具会直接说明，
        不要据此编造衣物。

        Args:
            scope(string): today=今天裁决出的这一身穿了什么（默认）；slot=指定部位的全部衣物；all=整份衣柜清单。只能填这三个值。
            slot(string): 仅 scope=slot 时需要。部位：upper 上装 / lower 下装 / whole 整身（连衣裙）/ feet 鞋 / extra 配件，也认「上装」「裙子」这类中文说法。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return self._wardrobe_detail_reply(
            scope=scope, slot=slot, user=self._wardrobe_intent_user(event)
        )

    @filter.llm_tool(name="pc_set_outfit_intent")
    @_multi_persona_event_context
    async def pc_set_outfit_intent(
        self,
        event: AstrMessageEvent,
        intent: str = "",
        items: str = "",
        outfit: str = "",
        **kwargs,
    ) -> str:
        """记录角色「本会话接下来穿什么」。用户明确要求换装、或剧情已经写出换衣过程时调用。

        记下之后，后续对话、日程与生图都以这套服装为准，直到用户再次换装，或今天结束
        （最长 12 小时）。只是想了解衣柜里有什么，请用 pc_query_wardrobe_detail，不要用本工具。

        Args:
            intent(string): 一句话说明换成什么，用用户原话最好，例如「换上泳衣」「今天想穿得清爽一点」。没有具体衣物时也要填这一项。
            items(string): 可选。衣柜里具体衣物的名称或编号，多个用逗号或顿号分隔，例如「浅蓝条纹衬衫,深蓝直筒牛仔裤」。只填你确认存在于衣柜里的；衣柜里没有的衣物不要填在这里，写进 intent 即可。
            outfit(string): 可选。整套的名称或编号，例如「通勤三件套」。与 items 同时给出时以整套为准。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return self._wardrobe_intent_reply(
            intent, items=items, outfit=outfit, user=self._wardrobe_intent_user(event)
        )

    @filter.llm_tool(name="pc_query_interaction")
    @_multi_persona_event_context
    async def pc_query_interaction(self, event: AstrMessageEvent, **kwargs) -> str:
        """查询 Bot 与某个私聊对象或群聊的近期互动摘要。

        Args:
            scope(string): private/group/auto。
            user_hint(string): 私聊对象/群成员 QQ、关系网名称、别名或显示名。
            group_hint(string): 群号或群名；和 user_hint 同时提供时查询这个人在该群的近期发言。
            hint(string): 不确定目标类型时的原始称呼。
            hours(number): 查询最近多少小时，默认 72。
            limit(number): 返回多少条候选互动线索，默认 36。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_query_interaction_impl(event, **kwargs)
