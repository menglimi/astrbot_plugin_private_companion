# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoPromptPart01Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_photo_prompt.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 385 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoPromptMixin）。
"""
from __future__ import annotations
from .llm_tool_actions_photo_prompt_shared import Any
from .llm_tool_actions_photo_prompt_shared import AstrMessageEvent
from .llm_tool_actions_photo_prompt_shared import PHOTO_TOOL_SILENT_SENTINEL
from .llm_tool_actions_photo_prompt_shared import PromptSection
from .llm_tool_actions_photo_prompt_shared import _render_tool_prompt_section_labeled_inline
from .llm_tool_actions_photo_prompt_shared import _single_line
from .llm_tool_actions_photo_prompt_shared import prompt_section
from .llm_tool_actions_photo_prompt_shared import re
from .llm_tool_actions_photo_prompt_shared import reaction_expression_explicit_opt_out
from .llm_tool_actions_photo_prompt_shared import reaction_expression_explicit_request
from .llm_tool_actions_photo_prompt_shared import reaction_expression_high_frequency
from .llm_tool_actions_photo_prompt_shared import reaction_expression_normalize_probability
from .llm_tool_actions_photo_prompt_shared import runtime_persona_setting



class LlmToolActionsPhotoPromptPart01Mixin:
    """LlmToolActionsPhotoPromptPart01Mixin（从 LlmToolActionsPhotoPromptMixin 拆出）。"""


    @staticmethod
    def _character_photo_request_matches(text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if any(
            marker in compact
            for marker in (
                "腿照",
                "脚照",
                "手照",
                "全身照",
                "半身照",
                "近照",
                "生活照",
                "穿搭照",
            )
        ):
            return True
        return bool(
            re.search(
                r"(?:看看|看下|看一下|想看|要看|让我看看|给我看看|发来看看).{0,10}"
                r"(?:腿|脚|手|脸|全身|半身|穿搭|衣服|样子)",
                compact,
                flags=re.I,
            )
        )

    def _photo_generation_instruction_matches(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        clauses = [
            part
            for part in re.split(
                r"(?:[，,。！？!?；;]+|但是|不过|然而|然后)", compact
            )
            if part
        ] or [compact]
        non_reaction_tokens = (
                "生图",
                "画图",
                "绘图",
                "生成图片",
                "出图",
                "画一张",
                "画张",
                "来张图",
                "来一张图",
                "自拍",
                "拍照",
                "照片",
                "相片",
                "头像",
                "壁纸",
                "改图",
                "修图",
                "重绘",
                "P图",
                "p图",
                "参考图",
                "穿搭图",
                "COS",
                "cosplay",
        )
        generated_reaction = re.compile(
            r"(?:生成|制作|做|画|绘制|设计|重绘).{0,10}"
            r"(?:表情包|贴纸|反应图|梗图)"
            r"|(?:表情包|贴纸|反应图|梗图).{0,10}"
            r"(?:生成|制作|做|画|绘制|设计|重绘)",
            flags=re.I,
        )
        rejected_generation = re.compile(
            r"(?:别|不要|不用).{0,8}(?:生成|制作|做|画|绘制|设计|重绘).{0,10}"
            r"(?:表情包|贴纸|反应图|梗图)",
            flags=re.I,
        )
        for clause in clauses:
            if reaction_expression_explicit_opt_out(clause):
                continue
            if self._character_photo_request_matches(clause):
                return True
            if any(token in clause for token in non_reaction_tokens):
                return True
            if rejected_generation.search(clause):
                continue
            if reaction_expression_explicit_request(clause):
                return True
            if generated_reaction.search(clause):
                return True
            if clause in {"斗图", "来斗图", "开始斗图"}:
                return True
        return False

    def _plaintext_photo_recovery_intent_matches(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        explicit_request = bool(
            re.search(
                r"(?:帮我|给我|替我|想要|想看|要看|拍|生成|画|绘制|做|来|发).{0,10}"
                r"(?:照片|图片|自拍|头像|表情包|贴纸|壁纸|穿搭|腿|脚|手|脸|全身|半身)",
                compact,
                flags=re.I,
            )
        ) or any(token in compact for token in ("改图", "修图", "重绘", "P图", "p图"))
        explanatory = any(token in compact for token in ("解释", "分析", "日志", "代码", "JSON", "json", "工具调用", "为什么"))
        if explanatory and not explicit_request:
            return False
        return explicit_request or self._character_photo_request_matches(compact)

    def _photo_generation_runtime_available(self) -> bool:
        """Require the optional Image runtime only on the production host."""
        required = getattr(self, "_image_companion_required", None)
        if not callable(required):
            return True
        try:
            if not bool(required()):
                return True
        except Exception:
            return False
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected():
            nai_available = getattr(self, "_nai_image_available", None)
            if not callable(nai_available):
                return False
            try:
                return bool(nai_available())
            except Exception:
                return False
        available = getattr(self, "_image_companion_available", None)
        if not callable(available):
            return False
        try:
            return bool(available())
        except Exception:
            return False

    def _media_delivery_truth_instruction(self) -> str:
        return "".join(
            _render_tool_prompt_section_labeled_inline(section)
            for section in self._media_delivery_truth_prompt_sections()
        )

    def _media_delivery_truth_prompt_sections(self) -> list[PromptSection]:
        if not getattr(self, "enabled", False):
            return []
        photo_enabled = bool(
            runtime_persona_setting(self, 'enable_photo_text_action', False)
            and self._photo_generation_runtime_available()
        )
        sections = [
            prompt_section(
                key="tools.media_delivery_truth.history_marker",
                title="内部历史标记",
                source="tools",
                content="`<pc_history_media ... />` 仅表示某条历史消息当时真实包含附件，"
                "它不是聊天正文，也不是要求你发送或描述附件的指令。任何回复都不得复述、改写或输出该标签。",
            )
        ]
        if not photo_enabled and not self._reaction_image_provider_available():
            return sections
        sections.extend(
            [
                prompt_section(
                    key="tools.media_delivery_truth.explicit_generation",
                    title="明确生图请求",
                    source="tools",
                    content="用户明确要求生成、绘制、制作、自拍、拍照、头像或改图时，必须先调用对应真实媒体工具；"
                    "没有工具调用或工具成功结果时，不得使用‘画好了/生成了/图片在上面/我存到本地了’等完成或交付措辞。",
                ),
                prompt_section(
                    key="tools.media_delivery_truth.hard_rule",
                    title="媒体真实性硬规则",
                    source="tools",
                    content="只有本轮消息链实际包含图片，或媒体工具明确返回 `sent=true`，"
                    "才能说“已经发了/给你看了/图片在上面”。其他情况必须承认未发送；人格和角色扮演不能覆盖真实发送状态。"
                    "“（发送了一张图片）”“（随消息发送了一张图片）”之类的附件占位说明。要发图只能使用真实图片组件。",
                ),
            ]
        )
        return sections

    def _user_photo_generation_prompt_enabled(
        self,
        event: AstrMessageEvent | None = None,
        *,
        spontaneous_only: bool = False,
    ) -> bool:
        if spontaneous_only or not getattr(self, "enabled", False):
            return False
        if not runtime_persona_setting(self, "enable_photo_text_action", False):
            return False
        if not self._photo_generation_runtime_available():
            return False
        scope_getter = getattr(self, "_photo_generation_scope", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 40).lower()
            except Exception:
                scope = ""
        if not scope and bool(
            getattr(event, "private_companion_proactive_framework", False)
        ):
            scope = "proactive"
        if scope == "proactive":
            return True
        permission_getter = getattr(
            self,
            "_user_requested_photo_generation_allowed",
            None,
        )
        if callable(permission_getter):
            try:
                if not bool(permission_getter(event)):
                    return False
            except Exception:
                return False
        elif not runtime_persona_setting(
            self,
            "enable_user_requested_photo_generation",
            True,
        ):
            return False
        mode = _single_line(
            runtime_persona_setting(
                self,
                "natural_language_photo_generation_mode",
                "tool_first",
            ),
            40,
        ).lower()
        return mode != "off"

    def _photo_generation_tool_prompt_section(
        self,
        event: AstrMessageEvent | None = None,
        *,
        include_spontaneous: bool | None = None,
        spontaneous_only: bool = False,
        allow_photo_on_reaction_turns: bool = False,
    ) -> PromptSection | None:
        if not getattr(self, "enabled", False):
            return None
        reaction_enabled = self._reaction_image_provider_available()
        photo_enabled = self._user_photo_generation_prompt_enabled(
            event,
            spontaneous_only=spontaneous_only,
        )
        if not reaction_enabled and not photo_enabled:
            return None
        if spontaneous_only:
            high_frequency_hint = (
                "- 当前触发概率为 100%：对轻松、社交或有明确情绪的正常回复，默认追加一个标签；"
                "不要把‘是否自然’再次当作概率筛选。纯事实、严肃、敏感或明确边界场景仍只发正文。"
                if reaction_expression_high_frequency(
                    runtime_persona_setting(self, 'reaction_expression_trigger_probability', 0.2)
                )
                else "- 轻松闲聊、玩笑、安慰、撒娇、庆祝、惊讶、接梗、轻吐槽，或‘收到/好的/笑死’这类语义明确的短回应，通常应在完整回复末尾追加内部标签。只有纯事实答复、严肃或敏感情境，或确实没有合适情绪时才省略。"
            )
            media_tool_boundary = (
                "不要使用 Markdown 代码块，不要解释标签，也不要调用图片或生图工具。"
                if not allow_photo_on_reaction_turns
                else "不要使用 Markdown 代码块，不要解释标签。"
            )
            spontaneous_lines = [
                    "- 先完成一条正常、完整、可以独立发送的文字回复。表情图片只能作为文字后的补充，绝对不能替代文字回复。",
                    "- 本轮已经由插件完成概率抽样并获得一次表情表达机会；不要再次按概率决定，也不要因为‘不确定’而默认省略标签。",
                    high_frequency_hint,
                    '-最小标签格式为 `<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无语","intensity":2}</pc_reaction_expression>`。',
                    "- `purpose` 写沟通用途，`emotion` 写希望传达的情绪，`intensity` 为 0-5；需要帮助检索时可选填 `candidate_queries`，提供 1-3 个简短说法。不要填写图片路径。",
                    f"- 每轮最多写一个标签，必须放在全部可见文字和 TTS 标签之后；{media_tool_boundary}",
                    "- 即使图库最终没有匹配、图片重复或发送失败，前面的完整文字也必须仍然自然成立。",
                ]
            if allow_photo_on_reaction_turns:
                spontaneous_lines.append(
                    "- 用户本轮明确提出“看看你/看照片/看自拍/看穿搭/展示穿着”等看图请求时，可以直接调用 `pc_generate_photo`（工具已在请求中提供），把完整正文写进 `caption` 随图发送；调用生图工具时不要再额外写表情标签。"
                )
                spontaneous_lines.append(
                    "- 没有明确看图请求时，不得主动调用 `pc_generate_photo`，只写文字或表情标签。"
                )
            return prompt_section(
                key="tools.reaction_expression",
                title="实验性表情表达",
                source="tools",
                content="\n".join(spontaneous_lines).strip(),
            )
        lines: list[str] = []
        # Only describe the gallery when its runtime provider is actually usable.
        if reaction_enabled and not spontaneous_only:
            reaction_availability = (
                "- 表情包素材库当前已有可用素材，用户请求现成表情包或反应图时可直接调用 `pc_find_reaction_image` 检索。"
                if reaction_enabled
                else "- 表情包素材库可能暂无可用的现成素材，用户仍可尝试调用 `pc_find_reaction_image` 检索；库为空时工具会返回对应提示。"
            )
            raw_probability = runtime_persona_setting(
                self, 'reaction_expression_trigger_probability', 0.2
            )
            if reaction_expression_high_frequency(raw_probability):
                spontaneous_hint = (
                    "- 自动追加表情包目前为高频触发：对轻松、社交或有明确情绪的动作/表情描述回复"
                    "（如[委屈巴巴地缩了缩脖子]、[开心地蹦跶了两下]），默认在正文后调用 `pc_find_reaction_image` "
                    "追加一个匹配表情包；纯事实、严肃、敏感或明确边界场景仍只发文字，不追加。"
                )
            else:
                chance = reaction_expression_normalize_probability(raw_probability, 0.2)
                spontaneous_hint = (
                    f"- 自动追加表情包是低概率点缀而非每轮默认动作：当前配置下约 {int(round(chance * 100))}% 的情境才自然带一个匹配表情包。"
                    "若回复中出现动作或表情描述（如[委屈巴巴地缩了缩脖子]、[开心地蹦跶了两下]），是典型的追加时机，"
                    "但请按上述概率自然把握：不要每轮都加，也不要因偶尔没加而向用户解释。"
                )
            lines.extend(
                [
                    reaction_availability,
                    "- 用户要“找/发/来一张已有表情包”、要用现成反应图回应当前语境时，优先使用 `pc_find_reaction_image`，把需求和当前语境写进 `query/search_context`。",
                    "- `pc_find_reaction_image` 在 `send=true` 时必须填写 `caption`，内容应是一条完整、自然、可独立成立的正文；图片只能追加在正文后，不能替代、缩短或省略正文。",
                    "- 决定调用 `pc_find_reaction_image` 时，把可见正文只放进 `caption` 参数；发起工具调用的同一轮不要再额外输出正文或声称图片已经发送。拿到工具结果后再按结果完成最终回复。",
                    "- 图库未匹配时可以自然改用文字回应，不要擅自声称已发图。",
                    spontaneous_hint,
                ]
            )
        if reaction_enabled:
            experiment_enabled = bool(
                runtime_persona_setting(self, 'enable_reaction_expression_experiment', False)
            )
            spontaneous_enabled = experiment_enabled and (
                include_spontaneous is not False
            )
            if spontaneous_enabled:
                lines.extend(
                    [
                        "- 普通闲聊中，先生成一条完整、可独立成立的文字回复；只有在正文之后追加表情图能明显补足语气、且符合本轮关系边界时，才可把 `spontaneous=true` 调用 `pc_find_reaction_image`。图片不能替代、缩短或省略正文；不要每轮调用，不确定时只保留自然文字回复。",
                        "- 发起自发表情工具调用时，把这条完整正文只放进 `caption` 参数，同一工具调用轮不要再额外输出正文或提前描述发送结果；等待工具结果后再完成最终回复。",
                        "- 自发表情调用应填写 `purpose`（沟通用途）、`emotion`（想传达的情绪）、`intensity`（0-5）与 `candidate_queries`（少量候选检索说法）；这些是本轮结构化表达意图，不要另行解释给用户。",
                        "- 自发表情允许因概率、冷却、重复或图库不匹配而返回 `decision=skip`。遇到跳过时不要解释内部原因，继续按原语境自然文字回复即可。",
                    ]
                )
        if photo_enabled:
            lines.append(
                "- 只有用户明确要求“生成/画/制作”新的角色表情包或贴纸时，才使用 `pc_generate_photo(kind=\"sticker\")`。不要把普通的现成表情包请求误当成生图。"
            )
            lines.extend(
                [
                    "- 用户明确要求生成图片、画图、出图、自拍、拍照、头像，或要求基于参考图改图时，可以使用 `pc_generate_photo`。",
                    '- 普通场景/物件/风景：仅当画面中不出现角色本人时，传 `{"prompt":"画面描述","kind":"text2img"}`，可用 `scene_preset` 建议“可拍画面/房间日常”；该字段只是建议，不会覆盖用户原话或参考图约束。把它写成角色镜头看到的画面，不要擅自加入拍摄者、陌生女孩或人物背影。纯梗图或无角色贴纸才用 `text2img + scene_preset="表情包场景"`。',
                    '- 角色本人以任何形式出镜，包括自拍、背影、侧脸、环境人像、头像、穿搭或 COS：传 `{"prompt":"画面要求","kind":"selfie"}`，可用 `scene_preset` 建议“角色自拍/COS自拍/日常穿搭/居家睡衣/镜前穿搭/头像特写”；明确睡衣、睡裙、睡袍或睡前卧室自拍时优先建议“居家睡衣”，普通穿搭才建议“日常穿搭”，只有明确“镜前/对镜/镜子”时才建议镜前穿搭；最终只采用一个兼容预设。只有开启参考图一致性时，未传参考图才会自动使用配置的人设参考图或今日穿搭参考图。',
                    '- 自拍也应延续角色此刻的生活状态。结合本轮已有的当前日程、位置和对话判断：如果角色正在上课、通勤或处理别的事，而用户想看海边、旅行地等明显不在当前现场的自拍，优先保持生活连续性，不要让角色像瞬间换了地点。用户只是想看这类画面时，通常可以自然理解为分享之前拍的、相册里的照片；仍可调用 `pc_generate_photo`，在 prompt 中说明按此前拍摄的照片呈现，并在 `caption` 里用角色口吻轻轻交代来源。',
                    '- 这不是固定拒绝规则。当前状态没有明显冲突、用户是在延续刚才的拍摄情境，或语境本来就是设想/COS/创作时，可以照常生成；只有用户明确强调“现在、立刻、现场拍”且与当前活动明显不合适时，再自然商量晚点拍。不要向用户复述内部日程判断或规则。',
                    '- 用户引用上一张角色照片并要求“比个心、看镜头、换个动作/表情/角度、再来一张”等自然续拍时，仍使用 `kind="selfie"`，并在 prompt 中说明只改变这次要求的部分、其余人物穿搭与场景继续保持；工具会读取本轮引用图片，不要猜测或手填图片路径。若本轮没有引用或携带图片，则按普通新自拍处理，选图器不会自动复用上一张成图。明确换装、换地点、换人物或另起主题时按新要求生成。',
                    '- 合影、合照、双人或多人同框必须有可验证的其他人物参考图：优先使用本轮携带或引用的图片；若请求明确点名了已在 Bot 关系网角色卡中绑定可用参考图的角色，也可直接调用 `pc_generate_photo` 并让工具自动选图，不要填写或猜测路径。Bot 单人人设图、今日穿搭图和纯文字关系卡都不算其他人物参考，模型自行填写的本地路径/URL 也不能单独授权合影。两类参考来源都没有时不要调用生图，也不要凭文字捏造另一张脸；可以说明需要先为该角色绑定参考图，或让用户发送/引用人物图片。',
                    '- 如果前几轮文字剧情已经明确让角色换装，而本轮只说“继续、再拍一张、保持刚才的穿搭”等，不要把它理解成恢复今日穿搭。必须把仍有效的具体服装展开写进 prompt，例如“角色当前仍穿 JK 校服，保持本轮地点和人物连续性”；当前对话已发生的换装高于日程、人格默认衣着、每日穿搭参考图和旧图片。',
                    '- “JK”在服装语境下请规范写成“JK 校服/JK 制服”；只有用户明确改变服装时才替换连续状态，提问、假设或用户自己换衣不算角色已换装。',
                    '- 角色表情包/贴纸：传 `{"prompt":"表情和画面要求","kind":"sticker"}`；默认走自拍/人像链路并使用“表情包场景”预设，让角色仍可识别。',
                    '- 改图/重绘：当前消息或引用消息已经带图时，传 `{"prompt":"修改要求","kind":"edit"}`，不要猜测、抄写或回传本地临时路径，插件会从当前事件安全取图。只有明确使用公网图片 URL 或插件已管理的参考图时才传 `reference_image_path`；多图职责组合可传 `reference_image_paths` 数组，并在 prompt 中说明每张图承担的脸、衣服、姿势等职责。没有任何当前/引用/已管理参考图时不要调用改图。',
                    "- `pc_generate_photo` 会自行发送成图，调用它之后绝对不要再调用 `pc_send_current_media`。如果另一个生图工具或图像编辑工具已经生成或编辑图片并明确返回了本地图片路径、且结果没有确认图片已发送，必须立即把该路径交给 `pc_send_current_media` 投递一次；不得回答“没法直接发”“图片存好了以后再看”。",
                    "- `pc_send_current_media` 只承接本轮或紧邻上一轮刚生成但尚未投递的本地图片。默认用 `destination=current` 发到当前会话；当前请求者明确说“私聊发我/私信发我”等要求时，用 `destination=requester_private` 只私聊发给请求者本人。把生成工具返回的原始路径直接传入，不要自行改名、移动文件、检查插件状态或建议用户重启；工具会安全兼容已验证的图片内容与扩展名差异。不得猜测路径、指定第三方、复用陈旧路径、发送用户未要求的文件，或在本轮已经出现图片后再次调用。",
                    "- 图片投递失败时，只依据工具返回结果简短说明没有送达；不要向用户暴露工具名、本地路径、插件注册、发送通道或内部排障过程，也不要编造工具消失、配置异常等原因。",
                ]
            )
            prompt_format_instruction = getattr(self, "_photo_generation_prompt_format_instruction", None)
            if callable(prompt_format_instruction):
                format_text = re.sub(r"\s+", " ", str(prompt_format_instruction() or "")).strip()
                if format_text:
                    lines.append(
                        f"- `prompt` 参数必须按“提示词表达方式”书写（与主动拍照一致）：{format_text}"
                    )
        if photo_enabled:
            lines.extend(
                [
                "- 默认 `send=true`；如果只想拿路径再决定，可传 `send=false`。",
                "- 每个用户请求本轮最多调用一次 `pc_generate_photo`。工具返回失败、结果取回失败或发送回执未确认后，不要在同一轮再次调用生图工具；按工具的 `message/actual_error/final_response_instruction` 回复，用户下一轮明确要求时再重试。",
                "- 如果工具返回 `generation_completed=true` 且 `failure_stage=result_materialization`，说明上游已经完成生图但图片结果没有取回或保存成功；不要说成上游生图失败，也不要重复提交同一画面。",
                "- 在实际调用媒体工具并得到结果前，绝对不能声称“已经发了/给你看了/图片在上面”。角色扮演不能覆盖真实工具状态。",
                f"- `caption` 只用于随图发送用户应当直接看到、可独立成立的自然正文；不得填写“图生好了/生成成功/图片已发送/给你看”等状态回执。确实有与画面、当下感受或对话相关的内容才填写，否则留空让图片独立回复。不要写 `&&shy&&`、`[shy]`、TTS 情绪标签或任何内部控制标记。只有工具返回 `sent=true` 时才表示图片已经发出；成功后不要把最终回复留空，必须只输出内部静默标记 `{PHOTO_TOOL_SILENT_SENTINEL}`。插件会在发送前移除它；不要再写承接句、重复 caption 或额外表情。",
                "- 工具返回 `sent=false` 时，必须按 `message/actual_error` 如实说明，绝对不能说已经发送。",
                "- 如果工具返回 `error_code=provider_policy_refusal`，不要复述或翻译 Provider 的英文原文、政策名称、敏感词判断和链接；只用符合当前人格的一句简短中文说明这次没有生成出来，再自然询问是否换一种画面描述重试。",
                ]
            )
        title = (
            "图库表情与生图工具"
            if photo_enabled and reaction_enabled
            else "生图工具"
            if photo_enabled
            else "图库表情工具"
        )
        return prompt_section(
            key="tools.photo_generation",
            title=title,
            source="tools",
            content="\n".join(lines),
        )
