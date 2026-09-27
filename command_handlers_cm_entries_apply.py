# -*- coding: utf-8 -*-
"""手册配置应用与条目域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 513 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import re
from .admin_config_command import (
    apply_config_value,
    apply_pending_config,
    apply_setting_command,
    can_apply_config,
    cancel_pending_config,
    get_pending_config,
    parse_setting_text,
)
from astrbot.api.event import AstrMessageEvent
from typing import Any



class CommandHandlersCmEntriesApplyMixin:
    """手册配置应用与条目域（从 CommandHandlersMixin 拆出）。"""


    def _companion_manual_can_apply_config(self, event: AstrMessageEvent) -> bool:
        return can_apply_config(self, event)

    def _companion_manual_get_pending_config(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        return get_pending_config(self, event)

    async def _companion_manual_apply_config_value(self, key: str, value: Any) -> tuple[bool, str, Any, Any]:
        return await apply_config_value(self, key, value)

    async def _companion_manual_apply_pending_config(self, event: AstrMessageEvent) -> str:
        return await apply_pending_config(self, event)

    def _companion_manual_cancel_pending_config(self, event: AstrMessageEvent) -> str:
        return cancel_pending_config(self, event)

    def _companion_manual_parse_setting_text(self, text: str) -> tuple[str, str]:
        return parse_setting_text(
            text,
            self._companion_manual_config_aliases(),
            self._companion_manual_config_key_from_alias,
        )

    async def _companion_manual_apply_setting_command(self, event: AstrMessageEvent, text: str) -> str:
        return await apply_setting_command(self, event, text)

    def _companion_manual_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "title": "管理命令、夹层密码和权限为什么用不了",
                "keywords": ["管理员命令", "管理权限", "管理员权限", "指令失效", "命令失效", "用不了命令", "不能用命令", "夹层密码", "输出夹层密码", "强制输出", "admins_id", "target_user_ids", "UMO", "UID", "default"],
                "summary": "陪伴插件的管理命令会在执行前先检查发送者用户 ID。AstrBot 全局管理员、本插件私聊目标用户，以及私聊页中关系角色设为主要用户的用户都具有管理权限；OneBot 通常是 QQ 号，QQ 官方机器人通常是 openid/平台用户 ID。",
                "checks": [
                    "可以在 AstrBot 全局管理员配置 admins_id 中填写真实用户 ID、加入插件私聊目标用户，或在插件私聊页把该用户的关系角色改为主要用户。",
                    "OneBot/aiocqhttp 通常填 QQ 号；QQ 官方机器人请填日志或私聊页显示的 openid/平台用户 ID。",
                    "target_user_ids 一行一个或用逗号分隔；优先直接填写用户 ID，误粘贴私聊 UMO 时会尝试提取 FriendMessage 后面的用户 ID。",
                    "不要放 default、aiocqhttp、UID、平台名或群聊会话串；这些不是私聊发送者用户 ID。",
                    "群管理员只用于群聊管理命令，不会自动获得私聊里的夹层密码、重置插件、生成日程等私聊管理权限。",
                    "夹层密码相关命令属于管理命令：陪伴 输出夹层密码、陪伴 强制输出 夹层密码、陪伴 重置夹层密码。",
                    "如果用户只是自然语言问“夹层密码是什么”，不会直接走管理输出；需要使用明确命令且通过权限检查。",
                ],
                "settings": [
                    "target_user_ids",
                ],
                "suggestions": [
                    "先让用户发：陪伴 状态，确认命令能被插件接管；如果提示需要管理权限，就检查 admins_id、私聊目标用户 ID或该用户的关系角色。",
                    "如果日志里看到 UID/default，说明查的是会话定位，不是权限 ID；权限配置要回到 event.get_sender_id() 对应的稳定用户 ID。",
                ],
            },
            {
                "title": "刚才被动消息为什么没回",
                "keywords": ["刚才没回", "刚才没回复", "刚刚没回", "为什么没回", "为什么不回", "没有回复", "被动未回复", "空结果", "跳过发送", "消息链全为空", "没发出来"],
                "summary": "这类问题先看最近被动未回复记录，而不是先猜人格。常见来源包括休息回复闸门、智能沉默、回复复核拦截、群聊答疑碰瓷复核、空结果兜底、自然语言生图或其他命令接管。",
                "checks": [
                    "排障页和答疑运行态都会合并最近被动未回复原因，重复原因不会刷屏。",
                    "如果来源是休息回复闸门，重点看 enable_rest_reply_simulation、rest_reply_mode 和 rest_reply_awake_grace_minutes。",
                    "如果来源是智能沉默，重点看 enable_smart_silence 和 smart_silence_min_confidence。",
                    "如果来源是群聊答疑复核，说明发送前判断这次回复像碰瓷插话。",
                    "如果来源是空结果/消息链全为空，要继续查哪个插件或工具阶段清空了结果。",
                ],
                "settings": [
                    "enable_rest_reply_simulation",
                    "rest_reply_mode",
                    "rest_reply_awake_grace_minutes",
                    "enable_smart_silence",
                    "smart_silence_min_confidence",
                    "enable_group_wakeup_question",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                ],
                "suggestions": [
                    "先问“陪伴 答疑 刚才为什么没回复”，让它带出最近未回复记录。",
                    "如果最近记录为空，再看 AstrBot 日志里 respond.stage、on_decorating_result 和插件命令是否提前 stop_event。",
                ],
            },
            {
                "title": "群聊老是不回复或好久才回复",
                "keywords": ["群聊", "群内", "群里", "不活跃", "活跃", "不回复", "没回复", "好久", "回复慢", "没反应", "不理", "卡住", "延迟"],
                "summary": "群聊回复不是所有消息都接管，通常要被 @、引用、命中唤醒、或处在连续对话窗口内。慢回复多半来自收口等待、高强度合并、模型超时或主链排队。",
                "checks": [
                    "先确认目标群启用了群聊陪伴，并且白名单/黑名单没有挡住。",
                    "如果没有 @/引用 Bot，只有“群聊连续对话保持”窗口内的同一用户后续发言才可能续接。",
                    "如果短时间连续叫 Bot，高强度收口会合并多条消息后再回复，看起来会慢几秒。",
                    "如果开启智能文本收口，短引子、逗号结尾、疑似没说完的话会先等补话。",
                    "如果日志里有 AstrBot 主链排队、Provider timeout、休息回复闸门或智能沉默，回复也可能被延后或取消。",
                ],
                "settings": [
                    "enable_group_companion",
                    "group_access_mode",
                    "enable_group_conversation_followup",
                    "enable_group_high_intensity_mode",
                    "enable_message_debounce",
                    "enable_smart_message_debounce",
                    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "想更容易接话：打开群聊连续对话，并把窗口设为 90-180 秒。",
                    "想少等：降低文本/短唤醒等待，或关闭智能文本收口。",
                    "高强度群里想减少误压制：阈值调到 4-5，持续时间调到 60-90 秒，合并范围改 same_user。",
                ],
            },
            {
                "title": "群聊连续对话在哪里设置",
                "keywords": ["连续对话", "续接", "上下文续接", "followup", "接话", "没at", "没@", "设置在哪"],
                "summary": "配置项是 enable_group_conversation_followup。开启后，群里同一用户明确 @/引用 Bot 之后，短时间内没继续 @ 的后续消息也会判断是否仍在和 Bot 对话。",
                "checks": [
                    "设置入口：配置页搜索“群聊连续对话保持”或 enable_group_conversation_followup。",
                    "窗口：group_conversation_followup_seconds，决定多久内还能续接。",
                    "轮数：group_conversation_followup_max_turns，决定不继续 @ 时最多自动续几轮。",
                    "模型：GROUP_FOLLOWUP_JUDGE_PROVIDER_ID 只在规则不确定时使用；留空时会先跟随快速响应模型，快速响应模型也留空时只走规则判断。",
                ],
                "settings": [
                    "enable_group_conversation_followup",
                    "group_conversation_followup_seconds",
                    "group_conversation_followup_max_turns",
                    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "推荐：窗口 120 秒，最多 1-2 轮，小模型可填低延迟分类模型。",
                    "如果经常碰瓷回复，把最大轮数设为 1，并填写续接判断模型。",
                ],
            },
            {
                "title": "群聊高强度收口和连续对话的关系",
                "keywords": ["高强度", "收口", "合并", "冲突", "连续对话", "压制", "高强度收口"],
                "summary": "两者不是硬冲突，但高强度收口优先级更高。近期连续叫到 Bot 时会合并明确消息，并暂停不确定的续接模型判断；冷却残留只降载，不再延迟单条明确 @。",
                "checks": [
                    "触发条件：group_high_intensity_wakeup_window_seconds 内唤醒次数达到 group_high_intensity_wakeup_threshold；唤醒疲劳只会在近期已有连续唤醒时辅助触发。",
                    "持续时间：group_high_intensity_cooldown_seconds。",
                    "合并范围：group 表示全群叫 Bot 合并；same_user 表示只合并同一用户补话。",
                    "高强度冷却期间明确 @/引用仍会处理；只有近期仍在连续叫 Bot 时才进入合并等待。",
                ],
                "settings": [
                    "enable_group_high_intensity_mode",
                    "group_high_intensity_wakeup_window_seconds",
                    "group_high_intensity_wakeup_threshold",
                    "group_high_intensity_cooldown_seconds",
                    "group_high_intensity_merge_seconds",
                    "group_high_intensity_merge_scope",
                ],
                "suggestions": [
                    "想保留对话感：阈值 4-5、持续 60-90 秒、合并范围 same_user。",
                    "想极限省 token：保持默认 group 合并，并接受高强度期间续接变保守。",
                ],
            },
            {
                "title": "消息收口/智能防抖是什么",
                "keywords": ["防抖", "收口", "智能收口", "补话", "等补充", "等待", "合并消息"],
                "summary": "消息收口会给用户留一点补充时间，把连续几句话合并成同一轮；智能收口会先判断这句话是不是完整，只有像“问你个事/你猜/等等/逗号结尾”才等待。",
                "checks": [
                    "总开关：enable_message_debounce。",
                    "智能文本收口：enable_smart_message_debounce。",
                    "固定文本等待：text_message_debounce_seconds；智能收口开启时主要看 smart_message_debounce_wait_seconds。",
                    "最长等待：text_message_debounce_max_wait_seconds，避免一直补话拖住。",
                    "最大合并：message_debounce_max_merge_messages，达到后立刻进入回复链。",
                ],
                "settings": [
                    "enable_message_debounce",
                    "enable_smart_message_debounce",
                    "text_message_debounce_seconds",
                    "smart_message_debounce_wait_seconds",
                    "text_message_debounce_max_wait_seconds",
                    "message_debounce_max_merge_messages",
                ],
                "suggestions": [
                    "觉得慢：文本等待设 0-2 秒，智能等待设 1-2 秒。",
                    "用户常先发图/转发再补字：图片/转发等待可以保留 5-8 秒。",
                ],
            },
            {
                "title": "群聊唤醒、@ 和答疑误触",
                "keywords": ["唤醒", "@", "at", "艾特", "答疑", "误触", "碰瓷", "为什么插话"],
                "summary": "群聊默认不会每句话都回复。明确 @/引用最强；名字、弱唤醒词、兴趣词、公共求助问题会按规则和概率进入回复链。答疑类回复发送前还有碰瓷复核。",
                "checks": [
                    "强触发：@ Bot、引用 Bot、直接叫 Bot 名字。",
                    "弱触发：group_wakeup_context_words、group_wakeup_interest_keywords、公共求助问题。",
                    "公共求助由 enable_group_wakeup_question 和 group_wakeup_question_threshold 控制。",
                    "答疑误触可看日志里的“群聊答疑回复发送前复核”。",
                ],
                "settings": [
                    "enable_group_wakeup_enhancement",
                    "group_wakeup_direct_words",
                    "group_wakeup_owner_direct_words",
                    "group_wakeup_context_words",
                    "group_wakeup_interest_keywords",
                    "enable_group_wakeup_question",
                    "group_wakeup_question_threshold",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：提高求助阈值，删掉泛化弱唤醒词，保留 Bot 名字和明确 @。",
                    "不回复多：检查是否被冷却/疲劳/高强度压制。",
                ],
            },
            {
                "title": "休息回复闸门和晚安后不回",
                "keywords": ["休息闸门", "休息回复", "睡眠闸门", "睡眠回复", "晚安", "睡觉", "睡眠", "不回消息", "说句晚安", "醒后补看"],
                "summary": "休息回复闸门只在 Bot 当前日程像睡眠/午休且处于配置窗口时生效。它会在回复链前判断要不要醒来；被拦截的私聊会记录到被动未回复，并可在醒后补看。",
                "checks": [
                    "总开关：enable_rest_reply_simulation；关闭后不会因为睡眠状态拦截被动回复。",
                    "模式：rest_reply_mode。probability 是概率醒来，llm 是小模型判断是否需要醒来。",
                    "模型阈值：rest_reply_llm_threshold，越高越不容易醒来。",
                    "清醒宽限：rest_reply_awake_grace_minutes，被叫醒后这段时间内不容易再次被当成休息中。",
                    "醒后补看：enable_rest_backlog_reply 和 rest_backlog_max_messages 控制被挡住的私聊是否下次注入。",
                ],
                "settings": [
                    "enable_rest_reply_simulation",
                    "rest_reply_mode",
                    "rest_reply_probability",
                    "rest_reply_llm_threshold",
                    "rest_reply_awake_grace_minutes",
                    "enable_rest_backlog_reply",
                    "rest_backlog_max_messages",
                    "REST_WAKEUP_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：先关闭 enable_rest_reply_simulation 止血，或改成 llm 模式并提高醒来判断质量。",
                    "只是晚安后不想整段断联：把清醒宽限调到 45-60 分钟。",
                    "想保留拟人睡眠：打开醒后补看，避免 token 已省但用户消息彻底丢上下文。",
                ],
            },
            {
                "title": "繁忙回复闸门和主动顺延",
                "keywords": ["繁忙闸门", "繁忙回复", "忙碌回复", "回复太快", "忙时延迟", "忙完再发", "主动顺延"],
                "summary": "繁忙回复闸门默认关闭。开启后只根据 Bot 当前日程和细化状态判断忙碌，不增加模型调用；普通被动消息会延迟但不会静默，普通主动消息会顺延到忙完并经过缓冲。",
                "checks": [
                    "总开关：enable_busy_reply_gate；关闭时被动和主动链路都保持原节奏。",
                    "私聊延迟：busy_reply_min_delay_seconds 到 busy_reply_max_delay_seconds；群聊会自动把最长等待限制到 12 秒。",
                    "紧急、安全风险和插件管理命令立即放行，不等待繁忙延迟。",
                    "主动缓冲：busy_reply_proactive_resume_buffer_minutes；普通主动候选顺延到当前忙碌片段结束后再等待这段时间。",
                    "用户预约、到期便签、环境突变、排障和模拟消息不参与主动顺延。",
                ],
                "settings": [
                    "enable_busy_reply_gate",
                    "busy_reply_min_delay_seconds",
                    "busy_reply_max_delay_seconds",
                    "busy_reply_proactive_resume_buffer_minutes",
                ],
                "suggestions": [
                    "默认私聊会等待 1-5 分钟；想让忙碌感更明显，可继续提高，但单项最多 15 分钟。",
                    "群聊无需单独配置；页面设置再大也会自动限制在 12 秒内。",
                ],
            },
            {
                "title": "智能沉默和结束话题",
                "keywords": ["智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话", "不想聊", "停止回复"],
                "summary": "智能沉默是在回复发送前工作的。主模型先生成回复，小模型再判断这轮是否应该安静收住；默认只看明确边界，也可以切到上下文模型判断。",
                "checks": [
                    "总开关：enable_smart_silence。",
                    "判断模式：smart_silence_judge_mode，boundary_only 只看明确边界，contextual 会把短句收尾、忙了/睡了、敷衍回应等也交给模型判断。",
                    "阈值：smart_silence_min_confidence，越高越不容易沉默。",
                    "模型：SMART_SILENCE_PROVIDER_ID；留空时会走插件模型回退。",
                    "群聊场景会参考最近真实群聊上下文，不只看唤醒消息。",
                    "如果已经发出去了，就不是智能沉默拦截；智能沉默的发送前兜底只会取消待发送回复。",
                ],
                "settings": [
                    "enable_smart_silence",
                    "smart_silence_judge_mode",
                    "smart_silence_min_confidence",
                    "smart_silence_model_timeout_seconds",
                    "SMART_SILENCE_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：把置信度调到 0.76-0.82，仍误触再临时关闭。",
                    "希望更准确：给 SMART_SILENCE_PROVIDER_ID 填低延迟但能稳定输出 JSON 的小模型。",
                ],
            },
            {
                "title": "回复复核/去重为什么会拦截",
                "keywords": ["回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截", "为什么被拦截"],
                "summary": "被动回复复核主要防止复读、串台、工具回执和异常文本外发；它与主动消息终审已经独立开关。",
                "checks": [
                    "先看最近被动未回复记录里 source 是“回复复核去重”“发送前拦截”还是“群聊答疑复核”。",
                    "passive_review_mode=full 时，普通被动回复也更容易进入模型改写；severe_only 更像默认保护层。",
                    "response_review_max_chars 太低时，短闲聊也可能被当成需要复核的长回复。",
                    "群聊答疑碰瓷复核不等同于普通去重，它只处理公共求助/答疑唤醒产生的可疑插话。",
                    "如果拦的是“消息已发送/发送成功”这类回执，通常是工具链回执被保护性拦截，不该关掉复核。",
                ],
                "settings": [
                    "enable_passive_response_review",
                    "passive_review_mode",
                    "passive_review_strength",
                    "response_review_max_chars",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                    "enable_group_wakeup_question",
                    "group_wakeup_question_threshold",
                ],
                "suggestions": [
                    "误杀普通回复：先使用 passive_review_strength=lenient；仍有问题可关闭 enable_passive_response_review，主动终审不会受影响。",
                    "只是群里答疑碰瓷被拦：优先调群聊解惑阈值，不要直接关整个复核。",
                ],
            },
            {
                "title": "回复太长或不听人格字数限制",
                "keywords": ["话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "口语化", "回复风格", "人设限制", "人格限制"],
                "summary": "人格里的字数限制仍然有效，但群聊高强度、关系网、动态状态、记忆、图片和转发上下文注入太多时，模型可能更想解释完整而变长。回复风格约束会作为更靠近当前请求的表达节奏提示注入。",
                "checks": [
                    "回复风格配置：reply_style_prompt，会进入普通聊天和主动消息的请求动态块。",
                    "它不是只作用于私聊；群聊主链、主动和被动都会尽量遵守，但复杂排障/教程可例外。",
                    "如果群聊只想极短，可以把句数、语言、口语化、复杂说明例外写清楚。",
                    "如果仍然超长，要看是否有其他插件或主人格在系统提示词里要求详细解释。",
                ],
                "settings": [
                    "reply_style_prompt",
                    "enable_group_high_intensity_mode",
                    "group_high_intensity_max_merge_messages",
                    "enable_message_debounce",
                ],
                "suggestions": [
                    "推荐写法：每次回复至多三句话；简单回答尽量 1-2 句；口语化、简洁；复杂问题或用户要求详细时例外。",
                    "群聊动态注入过多时，也可以降低高强度合并条数，减少一次性喂给模型的信息量。",
                ],
            },
            {
                "title": "模型和 Provider 配置",
                "keywords": ["llm", "LLM", "模型", "provider", "Provider", "配置模型", "没配置", "子模型", "小模型", "默认模型", "超时", "timeout", "降级", "无有效json"],
                "summary": "插件多数能力默认跟随 AstrBot 当前会话模型；部分功能可以单独指定 Provider。未单独配置时通常不是没模型，而是会回退到主模型或相关默认模型；模型超时或 JSON 无效时，部分判定会降级本地规则。",
                "checks": [
                    "主聊天回复通常使用 AstrBot 当前会话选择的人格和 Provider。",
                    "可以先用快速配置只填 4 类：快速响应模型、复杂推理模型、创作模型、插件视觉模型；高级单项留空时会自动套用这些快速配置。",
                    "陪伴答疑优先使用 TROUBLESHOOTING_PROVIDER_ID；快速配置下默认使用复杂推理模型，精准配置未填时优先回退到复杂推理/插件主模型。",
                    "智能收口使用 SMART_MESSAGE_DEBOUNCE_PROVIDER_ID；留空时跟随插件主模型。",
                    "生图模型不等于聊天模型，需要在生图平台/后端配置里单独确认。",
                    "日志出现 Request timed out、无有效 JSON、降级本地判定时，说明这次不是人格问题，而是某个小模型/主模型没在预算内稳定返回。",
                    "如果提示 Provider 不可用、模型超时或空回复，再看对应功能的 Provider ID 和 Token 页错误。",
                ],
                "settings": [
                    "LLM_PROVIDER_ID",
                    "FAST_RESPONSE_PROVIDER_ID",
                    "COMPLEX_REASONING_PROVIDER_ID",
                    "CREATIVE_MODEL_PROVIDER_ID",
                    "TROUBLESHOOTING_PROVIDER_ID",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                    "MAI_STYLE_PROVIDER_ID",
                    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
                    "SMART_SILENCE_PROVIDER_ID",
                    "PHOTO_MODEL_PROVIDER_ID",
                    "PHOTO_PROMPT_PROVIDER_ID",
                ],
                "suggestions": [
                    "只想先跑通：保留子模型为空，让它跟随主模型。",
                    "想降低延迟：答疑、智能收口、复核类功能可填低延迟小模型。",
                    "生图失败时优先检查生图平台和图片模型，不要填普通聊天模型。",
                ],
            },
            {
                "title": "“我会牢牢记住你”联动和未安装兼容",
                "keywords": ["rememberyou", "remember you", "我会牢牢记住你", "记忆插件", "专属记忆", "知识图谱", "未安装", "联动", "记忆查询"],
                "summary": "“我会牢牢记住你”是可选深度联动，不是陪伴插件硬依赖。安装后，陪伴插件会把日程、穿搭、创作、主动消息、用户习惯等结构化反馈给记忆插件；未安装时，本地关系网、状态和短期上下文仍照常工作。",
                "checks": [
                    "未安装“我会牢牢记住你”时，答疑、群聊、主动、状态模拟不会因此直接失效，只是少了长期结构化检索和图谱补充。",
                    "安装后，答疑会尝试读取近期排障/配置记忆，但本地运行状态、截图和日志仍优先。",
                    "私聊和群聊记忆边界仍由陪伴插件控制，不会把私聊隐私直接带进群聊。",
                    "如果联动导致超时，应该看到“我会牢牢记住你”读取失败或超时日志；陪伴插件会回退本地上下文。",
                ],
                "settings": [
                    "enable_companion_memory",
                    "enable_group_episode_memory",
                    "enable_group_privacy_guard",
                ],
                "suggestions": [
                    "想验证联动：先问穿搭、进食、最近主动消息这类明确事实，再看“我会牢牢记住你”是否有对应个人记忆。",
                    "担心 CPU 或超时：保持“我会牢牢记住你”为软依赖，不要把它放进每轮必须成功的阻塞链路。",
                ],
            },
            {
                "title": "主动消息不发或很少发",
                "keywords": ["主动", "不主动", "不发消息", "很久没发", "主动消息", "私聊主动"],
                "summary": "主动消息会受目标用户、每日上限、最小间隔、免打扰、休息状态、用户很久没回、主动价值复核和发送失败重试影响。",
                "checks": [
                    "确认用户 ID 已加入插件私聊目标用户，或已在私聊页启用。",
                    "检查 max_daily_messages、min_interval_minutes、quiet_hours。",
                    "如果用户长期不回，主动会变短、变少，甚至延后。",
                    "如果开启主动消息价值复核，低价值或像打扰的消息会被改写/拦截。",
                ],
                "settings": [
                    "target_user_ids",
                    "max_daily_messages",
                    "min_interval_minutes",
                    "quiet_hours",
                    "proactive_review_strength",
                    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "先用“陪伴 查看主动判定”和扩展页排障看最近一次跳过原因。",
                    "调试期把每日上限设 2-5，间隔不要太短，更容易观察真实行为。",
                ],
            },
            {
                "title": "拟人身体状态、饥饿和生理期",
                "keywords": ["饥饿", "一直饿", "一天都是饥饿", "胃口", "健康状态", "不适状态", "生理期", "姨妈", "来月经", "情绪太低", "情绪过低", "拟人状态"],
                "summary": "拟人身体状态来自日程、时间段、状态强度和条件状态。当前逻辑里，生理期模拟开关开启就视为适用；关闭后会清理旧的生理期条件。饥饿状态如果长时间不退，多半是强度偏高或进食记录没有被日程/记忆及时更新。",
                "checks": [
                    "健康状态：enable_health_state。",
                    "饥饿/胃口：enable_hunger_state。",
                    "生理期：enable_cycle_state，开启就认为适用模拟。",
                    "状态强度：humanized_state_intensity，越高越容易把状态写进提示词和主动候选。",
                    "如果接入“我会牢牢记住你”，进食、穿搭、习惯等会尽量以可检索的个人记忆补充，但未安装时插件仍能用本地状态运行。",
                ],
                "settings": [
                    "enable_health_state",
                    "enable_hunger_state",
                    "enable_cycle_state",
                    "humanized_state_intensity",
                ],
                "suggestions": [
                    "一天都饿：先关 enable_hunger_state 或把 humanized_state_intensity 降到 30-40。",
                    "不想要生理期模拟：直接关闭 enable_cycle_state。",
                    "情绪/身体状态压过人格：降低状态强度，不要只改人格。"
                ],
            },
            {
                "title": "生图/自拍/参考图问题",
                "keywords": ["生图", "自拍", "参考图", "图片", "画图", "改图", "不出图", "脸", "分辨率", "没反应", "好了", "穿搭图", "提示词"],
                "summary": "生图链路会优先使用配置的在线 API，失败后按配置回退；参考图一致性是可选子功能，开启后才会自动使用人设/穿搭参考图。非指令生图默认走工具优先，由主链模型调用 pc_generate_photo；规则快判只适合需要插件前置抢接单的场景。",
                "checks": [
                    "确认 enable_photo_text_action 和生图后端配置可用。",
                    "自拍/头像/角色表情包需要自动套人设或穿搭参考图时，先开启 enable_photo_reference_image；关闭时只按提示词生成。",
                    "非指令生图模式：natural_language_photo_generation_mode，可选 tool_first / rule_fast / off。",
                    "规则快判前置接管需要 enable_natural_language_photo_generation=true；显式指令和 pc_generate_photo 工具不依赖这个开关。",
                    "会话范围额度：photo_generation_private_owner_max_daily、photo_generation_private_friend_max_daily、photo_generation_group_max_daily、photo_generation_proactive_max_daily；每项均为 -1 不限量、0 不允许、正数为每日限额。",
                    "用户请求上限：command_photo_generation_max_daily，同时作用于显式陪伴生图指令和 pc_generate_photo 工具；-1 表示不限量，0 表示不允许，正数表示每日限额。",
                    "参考图命令：陪伴 参考图 <本地图片路径|图片URL|清空|查看>，也可带图或回复图片；查看会把当前实际参考图发出来检查。",
                    "多参考图库：发送一张或多张图片并使用“陪伴 参考图库 添加 <用途注释>”；支持列表、预览、删除和清空。用途注释写清服装、地点和适用场景，生图时会结合最终画面自动选一张，今日穿搭图不会无条件优先。",
                    "规则快判上限：natural_language_photo_generation_max_daily，只作用于 rule_fast。",
                    "分类型固定提示词：photo_generation_text2img_fixed_prompt、photo_generation_selfie_fixed_prompt、photo_generation_edit_fixed_prompt；分别作用于文生图、自拍/人像和改图，并经过清洗后写入生图日志。",
                    "兼容全局固定提示词：photo_generation_fixed_prompt 非空时仍叠加到所有类型；完全按类型控制时请留空。",
                    "提示词表达方式：photo_generation_prompt_format，可选 traditional（传统标签/短语）、natural_language（自然语言描述）或 nai（NAI 联动模式，按 NovelAI 4/4.5 标签语法书写并原样提交），全局作用于实际生图提示词。",
                    "Agnes Image 可选 platform=agnes，推荐 agnes-image-2.1-flash；参考图走 generations + extra_body.image，队列项可配置 1K-4K 与 ratio。",
                    "OpenRouter 可选 platform=openrouter；填写 https://openrouter.ai/api/v1，文生图走 /images/generations，参考图走 JSON /images + input_references。",
                    "MiniMax 可选 platform=minimax，国内站使用 https://api.minimaxi.com/v1/image_generation，模型填写 image-01 或 image-01-live；参考图走同一 JSON 接口的 subject_reference。",
                    "排障页可看最近生图提示词、参考图数量、后端错误和任务状态。",
                ],
                "settings": [
                    "enable_photo_text_action",
                    "natural_language_photo_generation_mode",
                    "photo_generation_private_owner_max_daily",
                    "photo_generation_private_friend_max_daily",
                    "photo_generation_group_max_daily",
                    "photo_generation_proactive_max_daily",
                    "command_photo_generation_max_daily",
                    "enable_natural_language_photo_generation",
                    "natural_language_photo_generation_max_daily",
                    "natural_language_photo_extra_prompt",
                    "enable_photo_reference_image",
                    "photo_reference_catalog",
                    "photo_generation_backend",
                    "photo_generation_prompt_format",
                    "photo_generation_fixed_prompt",
                    "photo_generation_text2img_fixed_prompt",
                    "photo_generation_selfie_fixed_prompt",
                    "photo_generation_edit_fixed_prompt",
                    "photo_generation_scene_presets",
                ],
                "suggestions": [
                    "如果误触多，先把 natural_language_photo_generation_mode 调回 tool_first，必要时改 off。",
                    "如果只有某类会话被禁止或额度用完，先检查对应的四项会话范围额度；不要用总额度代替范围控制。",
                    "如果只有用户明确请求提示额度用完或被禁止，检查 command_photo_generation_max_daily；设为 -1 即不限量，0 表示不允许，不要修改主动带图或规则快判上限。",
                    "如果没反应，先确认 enable_photo_text_action、生图后端、主链工具是否注册；只有 rule_fast 才看规则快判开关和每日上限。",
                    "如果出图后只回“好了”，重点看图片任务回调和结果说明模板，而不是聊天主模型。",
                    "在线 API 报模型错误时，确认图片模型不是普通聊天模型。",
                ],
            },
            {
                "title": "QQ 空间评论或说说链路",
                "keywords": ["qq空间", "空间", "说说", "评论", "回复评论", "一直回复", "onebot", "cookie", "点赞", "首次使用", "第一次使用", "登录空间"],
                "summary": "QQ 空间功能依赖 OneBot/Cookie 能力，评论收件箱默认应谨慎开启，并记录已见评论 ID 防止重复回复。",
                "checks": [
                    "QQ 空间属于陪伴本体保留的 OneBot 兼容能力，不依赖 astrbot_plugin_content_companion；先确认当前实例检测到可用 OneBot/aiocqhttp，再确认 enable_qzone_integration 和对应子功能开启。",
                    "第一次使用时，先在 OneBot/适配器所在账号完成 QQ 空间登录；排障提示“请先登录空间”通常表示 Cookie 不可用或已失效。QQ 官方机器人不会显示可用的 QQ 空间入口。",
                    "如果日志提示没有可用 OneBot 连接，通常是当前适配器没有暴露可取 Cookie 的连接。",
                    "点赞失效时优先跑排障页 QQ 空间测试，看 Cookie、g_tk、说说列表和点赞接口哪一步失败。",
                    "重复回复同一评论时，重点看 comment_inbox_seen_ids / replied_ids 是否保存。",
                    "可在排障页跑 QQ 空间测试。",
                ],
                "settings": [
                    "enable_qzone_integration",
                    "enable_qzone_life_publish",
                    "enable_qzone_comment_inbox",
                    "qzone_comment_inbox_interval_minutes",
                    "qzone_comment_inbox_recent_posts",
                    "qzone_comment_inbox_max_replies_per_tick",
                ],
                "suggestions": [
                    "评论回复建议低概率、长间隔、默认关闭，先用测试链路确认不会重复回复。",
                ],
            },
        ]

    def _companion_manual_select_entries(self, query: str) -> list[dict[str, Any]]:
        compact = re.sub(r"\s+", "", query).lower()
        issue_tags = self._companion_manual_issue_tags(query)
        entries = self._companion_manual_entries()
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in entries:
            score = 0
            entry_tags = self._companion_manual_entry_tags(entry)
            for keyword in entry.get("keywords", []):
                key = re.sub(r"\s+", "", str(keyword or "")).lower()
                if key and key in compact:
                    score += max(2, min(8, len(key)))
            title = re.sub(r"\s+", "", str(entry.get("title") or "")).lower()
            if title and any(part and part in compact for part in re.split(r"[、/ ]+", title)):
                score += 2
            overlap = issue_tags & entry_tags
            if overlap:
                score += 6 * len(overlap)
            if "recent" in issue_tags and entry_tags & {"rest", "silence", "debounce", "group", "photo"}:
                score += 2
            if "location" in issue_tags and any(str(item or "").strip() for item in entry.get("settings", [])):
                score += 1
            if issue_tags and entry_tags and not overlap:
                score -= 3
            if "photo" in issue_tags and entry_tags & {"group", "debounce"} and "group" not in issue_tags:
                score -= 8
            if "silence" in issue_tags and entry_tags == {"group"}:
                score -= 8
            if "rest" in issue_tags and entry_tags & {"group", "photo"}:
                score -= 6
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:3]]
