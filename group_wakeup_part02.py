# -*- coding: utf-8 -*-
"""GroupWakeupPart02Mixin。

由 tools/split_mixin_domain.py 从 group_wakeup.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 369 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupWakeupMixin）。
"""
from __future__ import annotations

from .group_wakeup_shared import _persona_value
from .group_wakeup_shared import Any
from .group_wakeup_shared import _group_link_message_context
from .group_wakeup_shared import _now_ts
from .group_wakeup_shared import _safe_float
from .group_wakeup_shared import _safe_int
from .group_wakeup_shared import _single_line
from .group_wakeup_shared import re



class GroupWakeupPart02Mixin:
    """GroupWakeupPart02Mixin（从 GroupWakeupMixin 拆出）。"""


    def _group_wakeup_topic_interest_context(
        self,
        group: dict[str, Any],
        *,
        sender_id: str,
        text: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        now = _now_ts()
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        recent_texts = [
            _single_line(item.get("text"), 160)
            for item in recent[-8:]
            if isinstance(item, dict)
            and now - _safe_float(item.get("ts"), 0) <= 8 * 60
            and (not sender_id or str(item.get("sender_id") or "") == str(sender_id))
            and _single_line(item.get("text"), 160)
        ]
        buffer = {}
        if group_id and sender_id:
            buffer = self._semantic_buffer_active_snapshot(self._semantic_buffer_key(f"group:{group_id}", sender_id))
        buffer_texts = buffer.get("texts") if isinstance(buffer.get("texts"), list) else []
        signature = self._group_topic_signature(text)
        active_topic = None
        threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
        for item in threads[:8]:
            if not isinstance(item, dict):
                continue
            if now - _safe_float(item.get("last_ts"), 0) > 45 * 60:
                continue
            if signature and self._topic_signature_similar(signature, str(item.get("signature") or "")):
                active_topic = item
                break
            title = _single_line(item.get("title"), 80)
            if title and (title in text or text in title):
                active_topic = item
                break
        if not isinstance(active_topic, dict):
            active_topic = next(
                (
                    item for item in threads[:4]
                    if isinstance(item, dict) and now - _safe_float(item.get("last_ts"), 0) <= 12 * 60
                ),
                None,
            )
        topic_texts: list[str] = []
        if isinstance(active_topic, dict):
            topic_texts.append(_single_line(active_topic.get("title"), 80))
            examples = active_topic.get("recent_examples") if isinstance(active_topic.get("recent_examples"), list) else []
            topic_texts.extend(_single_line(item.get("text"), 100) for item in examples[-5:] if isinstance(item, dict))
        return {
            "recent_texts": [item for item in recent_texts if item],
            "buffer_texts": [_single_line(item, 160) for item in buffer_texts if _single_line(item, 160)],
            "buffer_active": bool(buffer.get("active")),
            "topic": active_topic if isinstance(active_topic, dict) else {},
            "topic_texts": [item for item in topic_texts if item],
        }

    def _group_wakeup_topic_interest_weight(
        self,
        group: dict[str, Any],
        word: str,
        *,
        sender_id: str,
        text: str,
        group_id: str = "",
    ) -> dict[str, Any]:
        token = _single_line(word, 60)
        if not token:
            return {"multiplier": 1.0, "score": 0.0, "reason": ""}
        context = self._group_wakeup_topic_interest_context(group, sender_id=sender_id, text=text, group_id=group_id)
        score = 0.0
        reasons: list[str] = []
        if self._text_contains_wakeup_word(text, token):
            score += 1.0
            reasons.append("当前句命中")
        recent_hits = sum(1 for item in context.get("recent_texts", []) if self._text_contains_wakeup_word(str(item), token))
        if recent_hits:
            score += min(1.2, recent_hits * 0.35)
            reasons.append(f"近句{recent_hits}次")
        buffer_hits = sum(1 for item in context.get("buffer_texts", []) if self._text_contains_wakeup_word(str(item), token))
        if buffer_hits:
            score += min(0.9, buffer_hits * 0.3)
            reasons.append(f"收口中{buffer_hits}次")
        topic_hits = sum(1 for item in context.get("topic_texts", []) if self._text_contains_wakeup_word(str(item), token))
        if topic_hits:
            topic = context.get("topic") if isinstance(context.get("topic"), dict) else {}
            participants = topic.get("participants") if isinstance(topic.get("participants"), list) else []
            messages = _safe_int(topic.get("message_count"), 0, 0)
            score += min(1.6, 0.55 + topic_hits * 0.22 + min(0.35, len(participants) * 0.05) + min(0.35, messages * 0.03))
            reasons.append("话题线升温")
        boost_getter = getattr(self, "_effective_group_wakeup_topic_interest_max_boost", None)
        max_boost = (
            boost_getter()
            if callable(boost_getter)
            else max(0.0, min(1.5, float(_persona_value(self, "group_wakeup_topic_interest_max_boost", 0.45) or 0.0)))
        )
        multiplier = 1.0 + min(max_boost, score * 0.16)
        if context.get("buffer_active"):
            penalty = max(0.0, min(1.0, float(_persona_value(self, "group_wakeup_debounce_pending_penalty", 0.65) or 0.0)))
            multiplier *= max(0.05, 1.0 - penalty)
            reasons.append("同轮收口降权")
        return {
            "multiplier": round(max(0.0, multiplier), 3),
            "score": round(score, 2),
            "reason": "、".join(reasons[:4]),
            "buffer_active": bool(context.get("buffer_active")),
            "recent_texts": list(context.get("recent_texts") or [])[-4:],
            "topic_texts": list(context.get("topic_texts") or [])[-4:],
        }

    def _group_wakeup_probability_context(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        probability: float,
        wakeup_type: str,
    ) -> tuple[float, dict[str, Any]]:
        probability = max(0.0, min(1.0, float(probability or 0.0)))
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        pace = str(atmosphere.get("pace") or "")
        mood = str(atmosphere.get("mood") or "")
        if pace == "热闹":
            probability *= 0.55 if wakeup_type != "question" else 0.72
        elif pace == "安静" and wakeup_type in {"question", "cold_group"}:
            probability *= 1.15
        if mood == "求助" and wakeup_type == "question":
            probability *= 1.35
        if str(scene.get("trigger") or "") in {"reply_in_flow", "quick_follow"}:
            probability *= 0.65
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        mood_bias = str(state.get("mood_bias") or "") if isinstance(state, dict) else ""
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"}:
            probability *= 0.18
        if energy <= 35:
            probability *= 0.55
        elif energy >= 82:
            probability *= 1.12
        if any(token in mood_bias for token in ("无聊", "烦闷", "好奇", "兴奋")):
            probability *= 1.15
        fatigue = self._group_wakeup_fatigue(group)
        if str(fatigue.get("level") or "") == "medium":
            probability *= 0.65
        elif str(fatigue.get("level") or "") == "high":
            probability *= 0.35
        return min(0.95, max(0.0, probability)), fatigue

    def _group_wakeup_question_signal(self, text: str) -> dict[str, Any]:
        original = _single_line(text, 1000)
        cleaned, _ = _group_link_message_context(original)
        if len(cleaned) < 4:
            return {}
        if re.search(r"(```|\[图片\]|\[语音\]|\[视频\]|\[转发消息\])", cleaned, flags=re.I):
            return {}
        if re.search(r"(哈哈|草|笑死|绷不住|乐|乐死|不是吧|不会吧).{0,8}[?？]?$", cleaned):
            return {}
        if re.search(r"(急急如律令|急急国王|急急急急|急死谁了)", cleaned):
            return {}
        failure_context_pattern = r"(安装|配置|运行|启动|登录|请求|发送|上传|下载|连接|编译|构建|部署|调用|识别|读取|解析|保存|加载|同步|执行|支付|打开|导入|导出|更新|提交|注册|验证|接口|插件|程序|脚本|命令|模型|图片|语音|视频|文件|消息|任务).{0,8}失败|(?:一直|总是|老是|反复|还是|又).{0,6}失败|失败.{0,8}(怎么办|咋办|怎么弄|怎么解决|怎么处理|原因|报错|日志|重试|一直|总是|还是|又)"
        help_context_pattern = r"(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|能不能|有没有人|有人懂|谁会|报错|异常|error|traceback|bug|卡住|跑不起来|急求|急问|在线等)"
        plain_help_meme = bool(re.search(r"救命", cleaned)) and not bool(re.search(help_context_pattern, cleaned, flags=re.I))
        if plain_help_meme and not (re.search(r"[?？]$", cleaned) and re.search(r"(怎么|咋办|怎么办|能不能|有没有|谁|哪|什么|啥)", cleaned)):
            return {}
        score = 0
        reason = ""
        help_type = "解释"
        urgent_help = r"(急求|急问|急用|急等|在线等|有点急|很急|比较急|挺急|急着|崩了|寄了|炸了|跑不起来|过不去|卡住|报错|异常|error|traceback|bug)"
        if re.search(urgent_help, cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I):
            score += 18
            help_type = "排障"
        if re.search(r"(报错|异常|error|traceback|bug|日志|堆栈|闪退|崩溃|跑不起来)", cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I):
            help_type = "排障"
        elif re.search(r"(怎么弄|怎么做|怎么搞|如何|教程|步骤|配置|安装|使用)", cleaned):
            help_type = "操作"
        elif re.search(r"(这是什么|这个是什么|看得懂|识别|图里|截图)", cleaned):
            help_type = "识别"
        elif re.search(r"(啥意思|什么意思|为什么|为啥|咋回事|怎么回事|什么情况|啥情况)", cleaned):
            help_type = "解释"
        strong_patterns = (
            (r"(有没有|有无|有没有人|有人|谁|哪位|大佬).{0,12}(懂|知道|会|看得懂|能解释|能帮|帮忙)", 76, "open_help"),
            (r"(求问|请教|咋办|怎么办|怎么弄|怎么解决|怎么处理|怎么搞)", 80, "help_request"),
            (r"救命.{0,12}(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|报错|异常|卡住|跑不起来)", 80, "help_request"),
            (r"(为什么|为啥|咋回事|怎么回事|什么情况|啥情况|啥意思|什么意思)", 68, "explain_question"),
            (r"(这是什么|这个是什么|这个咋|这个怎么|这个为啥|这个能不能|这能不能)", 64, "identify_question"),
            (r"(值不值得|值得(?:买|入手|用)?吗|靠谱吗|能买吗|能用吗|好用吗|怎么样|咋样|推荐吗|合适吗)", 68, "evaluation_question"),
        )
        for pattern, base_score, base_reason in strong_patterns:
            if re.search(pattern, cleaned):
                score = max(score, base_score)
                reason = base_reason
                break
        if re.search(r"[?？]$", cleaned) and re.search(r"(怎么|为什么|为啥|什么|啥|哪|谁|能不能|可以吗|行吗|会不会|是不是|有没有)", cleaned):
            score = max(score, 54)
            reason = reason or "question_mark"
        if score <= 0:
            return {}
        intensity = "高" if score >= 85 else ("中" if score >= 70 else "低")
        reason = reason or "question"
        return {
            "word": "疑问",
            "reason": reason,
            "reason_label": self._group_wakeup_reason_label("question", reason),
            "score": min(100, score),
            "intensity": intensity,
            "help_type": help_type,
            "raw_text": cleaned,
        }

    def _group_wakeup_question_context_gate(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        text: str,
        signal: dict[str, Any],
    ) -> tuple[bool, list[str], int]:
        cleaned = _single_line(text, 260)
        reason = _single_line(signal.get("reason"), 60)
        base_score = _safe_int(signal.get("score"), 0, 0, 100)
        help_type = _single_line(signal.get("help_type"), 24)
        trigger = str(scene.get("trigger") or "")
        notes: list[str] = []
        penalty = 0
        strong_public_help = (
            reason in {"open_help", "help_request"}
            or help_type in {"排障", "操作"}
            or bool(
                re.search(
                    r"(有没有人|有人|谁|哪位|大佬).{0,14}(懂|知道|会|能帮|帮忙)|"
                    r"(求问|请教|急求|急问|在线等|帮忙|帮我)|"
                    r"(报错|异常|error|traceback|bug|日志|堆栈|闪退|崩溃|跑不起来|卡住|失败)",
                    cleaned,
                    flags=re.I,
                )
            )
        )
        conversational_only = bool(
            re.search(
                r"^(?:啊|诶|欸|哈|哈哈|草|不是|不会吧|真的假的|所以|那|这|啥|为什么|为啥|怎么|咋|什么情况|啥情况)[，,。.\s]*[^，,。！？!?]{0,24}[?？]?$",
                cleaned,
            )
        )
        if trigger in {"reply_in_flow", "quick_follow"}:
            if strong_public_help:
                penalty += 8
                notes.append("接话场景但像公共求助")
            else:
                penalty += 28 if base_score < 76 else 18
                notes.append("上下文像在接别人话")
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        now = _now_ts()
        recent_other_messages = [
            item
            for item in recent[-6:]
            if isinstance(item, dict)
            and _safe_float(item.get("ts"), 0.0, 0.0) > 0
            and now - _safe_float(item.get("ts"), 0.0, 0.0) <= 90
        ]
        if conversational_only and not strong_public_help:
            penalty += 18
            notes.append("短反问/吐槽句")
        if recent_other_messages and not strong_public_help:
            last_text = _single_line(recent_other_messages[-1].get("text"), 160)
            if last_text and re.search(r"(就是|因为|所以|应该|可以|不用|不是|这个|那个|确实|对|已经|试试|看看)", cleaned):
                penalty += 10
                notes.append("疑似延续群内讨论")
            answered_like = any(
                re.search(r"(可以|应该|因为|原因|解决|试试|改成|换成|用|装|配置|设置|报错|日志|看起来|大概)", _single_line(item.get("text"), 160))
                for item in recent_other_messages[-3:]
            )
            if answered_like and reason in {"explain_question", "question_mark", "question"}:
                penalty += 8
                notes.append("附近已有讨论/回答")
        if re.search(r"(你们|你俩|他|她|它|这人|那人|楼上|上面|群主|管理员|作者|大佬).{0,16}[?？]$", cleaned) and not strong_public_help:
            penalty += 22
            notes.append("问题目标不像 Bot")
        block = False
        if not strong_public_help and trigger in {"reply_in_flow", "quick_follow"} and base_score - penalty < 60:
            block = True
        if not strong_public_help and conversational_only and base_score - penalty < 65:
            block = True
        return block, notes, penalty

    def _group_wakeup_question_score_context(
        self,
        group: dict[str, Any],
        scene: dict[str, Any],
        signal: dict[str, Any],
    ) -> tuple[int, dict[str, Any], list[str]]:
        score = max(0, min(100, _safe_int(signal.get("score"), 0, 0)))
        notes: list[str] = []
        atmosphere = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        pace = str(atmosphere.get("pace") or "")
        mood = str(atmosphere.get("mood") or "")
        if pace == "热闹":
            score -= 8
            notes.append("热闹降权")
        elif pace == "安静":
            score += 4
            notes.append("安静加权")
        if mood == "求助":
            score += 10
            notes.append("求助气氛")
        if str(scene.get("trigger") or "") in {"reply_in_flow", "quick_follow"}:
            score -= 10
            notes.append("疑似接别人话")
        context_blocked, context_notes, context_penalty = self._group_wakeup_question_context_gate(
            group,
            scene,
            _single_line(signal.get("text"), 260) or _single_line(signal.get("raw_text"), 260) or "",
            signal,
        )
        if context_penalty:
            score -= context_penalty
            notes.extend(context_notes)
        if context_blocked:
            score = min(score, 49)
            if "上下文门控" not in notes:
                notes.append("上下文门控")
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"}:
            score -= 35
            notes.append("休息降权")
        if energy <= 35:
            score -= 8
            notes.append("低能量降权")
        fatigue = self._group_wakeup_fatigue(group)
        if str(fatigue.get("level") or "") == "medium":
            score -= 10
            notes.append("疲劳降权")
        elif str(fatigue.get("level") or "") == "high":
            score -= 22
            notes.append("高疲劳降权")
        return max(0, min(100, score)), fatigue, notes

    def _group_wakeup_cold_group_signal(self, group: dict[str, Any], text: str, now: float) -> dict[str, Any]:
        if not bool(_persona_value(self, "enable_group_wakeup_cold_group", False)):
            return {}
        idle_minutes = max(3, _safe_int(_persona_value(self, "group_wakeup_cold_group_idle_minutes", 25), 25, 3))
        last_seen = _safe_float(group.get("last_seen"), 0)
        if last_seen <= 0 or now - last_seen < idle_minutes * 60:
            return {}
        cleaned = _single_line(text, 260)
        if not (3 <= len(cleaned) <= 120):
            return {}
        if re.search(r"^(?:[/!！#]|签到|打卡|菜单|帮助|help\b)", cleaned, flags=re.I):
            return {}
        if re.search(r"(https?://|www\.|```|\[图片\]|\[语音\]|\[视频\]|\[转发消息\])", cleaned, flags=re.I):
            return {}
        state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        runtime = state.get("sleep_runtime") if isinstance(state, dict) and isinstance(state.get("sleep_runtime"), dict) else {}
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        if phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"} or energy <= 35:
            return {}
        direct = re.search(r"(有人吗|在吗|还在吗|睡了吗|怎么没人|好安静|冷群|冒泡|路过|诈尸|开门|醒醒)", cleaned)
        if direct:
            return {"word": "冷群", "reason": "cold_group_opening", "reason_label": self._group_wakeup_reason_label("cold_group", "cold_group_opening"), "score": 86, "idle_seconds": round(now - last_seen, 1)}
        if re.search(r"(早|早上好|上午好|中午好|下午好|晚上好|晚好|嗨|hello|hi|哈喽|冒个泡|有人不|人呢)", cleaned, flags=re.I):
            return {"word": "冷群", "reason": "cold_group_greeting", "reason_label": self._group_wakeup_reason_label("cold_group", "cold_group_greeting"), "score": 72, "idle_seconds": round(now - last_seen, 1)}
        if re.search(r"(急急如律令|急急国王|急急急急|急死谁了)", cleaned):
            return {}
        failure_context_pattern = r"(安装|配置|运行|启动|登录|请求|发送|上传|下载|连接|编译|构建|部署|调用|识别|读取|解析|保存|加载|同步|执行|支付|打开|导入|导出|更新|提交|注册|验证|接口|插件|程序|脚本|命令|模型|图片|语音|视频|文件|消息|任务).{0,8}失败|(?:一直|总是|老是|反复|还是|又).{0,6}失败|失败.{0,8}(怎么办|咋办|怎么弄|怎么解决|怎么处理|原因|报错|日志|重试|一直|总是|还是|又)"
        if re.search(r"救命", cleaned) and not (re.search(r"(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|报错|异常|卡住|跑不起来|急求|急问|在线等)", cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I)):
            return {}
        if re.search(r"(救命.{0,12}(怎么|咋办|怎么办|怎么弄|怎么解决|怎么处理|帮忙|帮我|报错|异常|卡住|跑不起来)|急求|急问|急用|急等|在线等|有点急|很急|比较急|挺急|急着|求问|请教|有没有人|有人懂|谁会|帮忙|咋办|怎么办|怎么弄|报错|崩了|卡住)", cleaned, flags=re.I) or re.search(failure_context_pattern, cleaned, flags=re.I):
            return {"word": "冷群", "reason": "cold_group_help", "reason_label": self._group_wakeup_reason_label("cold_group", "cold_group_help"), "score": 78, "idle_seconds": round(now - last_seen, 1)}
        if re.search(r"[?？]$", cleaned):
            return {}
        return {}
