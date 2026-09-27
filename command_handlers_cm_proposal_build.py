# -*- coding: utf-8 -*-
"""配置提案构建域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 795 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import re
from .command_handlers_shared import logger
from .helpers import _flat_get, _safe_float, _safe_int, _single_line
from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS
from astrbot.api.event import AstrMessageEvent
from typing import Any



class CommandHandlersCmProposalBuildMixin:
    """配置提案构建域（从 CommandHandlersMixin 拆出）。"""


    def _companion_manual_issue_tags(self, query: str) -> set[str]:
        compact = re.sub(r"\s+", "", str(query or "")).lower()
        tags: set[str] = set()
        if not compact:
            return tags
        if any(word in compact for word in ("刚才", "这次", "刚刚", "上一条", "为什么没回", "为什么不回", "没有回复", "不回复", "没回复")):
            tags.add("recent")
        if any(word in compact for word in ("群聊", "群里", "群内", "群消息", "没@", "没at", "连续对话", "高强度", "收口", "唤醒", "插话", "碰瓷")):
            tags.add("group")
        if any(word in compact for word in ("连续对话", "续接", "接话", "没@", "没at")):
            tags.add("followup")
        if any(word in compact for word in ("高强度", "收口", "合并", "压制")):
            tags.add("high_intensity")
        if any(word in compact for word in ("防抖", "智能收口", "补话", "等补充", "合并消息")):
            tags.add("debounce")
        if any(word in compact for word in ("休息闸门", "休息回复", "睡眠闸门", "睡眠回复", "晚安", "睡觉", "睡眠", "醒后补看")):
            tags.add("rest")
        if any(word in compact for word in ("智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话", "不想聊")):
            tags.add("silence")
        if any(word in compact for word in ("回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截")):
            tags.add("review")
        if any(word in compact for word in ("生图", "画图", "改图", "自拍", "参考图", "穿搭图", "出图", "提示词", "图片生成", "自然语言生图")):
            tags.add("photo")
        if any(word in compact for word in ("qq空间", "空间", "说说", "评论", "点赞", "cookie", "onebot", "登录空间")):
            tags.add("qzone")
        if any(word in compact for word in ("饥饿", "饿", "胃口", "生理期", "姨妈", "健康状态", "不适状态", "情绪太低", "情绪过低", "拟人状态")):
            tags.add("state")
        if any(word in compact for word in ("话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "口语化", "回复风格")):
            tags.add("style")
        if any(word in compact for word in ("模型", "provider", "llm", "超时", "timeout", "降级", "无有效json", "无效json")):
            tags.add("model")
        if any(word in compact for word in ("rememberyou", "remember you", "我会牢牢记住你", "记忆插件", "知识图谱", "专属记忆", "未安装")):
            tags.add("memory")
        if any(word in compact for word in ("管理员命令", "管理权限", "管理员权限", "指令失效", "命令失效", "用不了命令", "不能用命令", "夹层密码", "输出夹层密码", "强制输出", "admins_id", "target_user_ids", "umo", "uid", "default")):
            tags.add("permission")
        if any(word in compact for word in ("在哪", "哪里", "位置", "设置", "配置项", "怎么改", "如何改", "调参")):
            tags.add("location")
        return tags

    def _is_companion_manual_natural_permission_question(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        permission_terms = (
            "管理员命令", "管理命令", "管理权限", "管理员权限", "私聊管理员", "私聊管理",
            "夹层密码", "资料柜密码", "抽屉密码", "输出夹层密码", "强制输出", "重置夹层密码",
            "admins_id", "adminsid", "target_user_ids", "targetuserids", "umo", "uid", "default",
        )
        problem_terms = (
            "用不了", "不能用", "没法用", "无法用", "失效", "不生效", "没反应", "不识别",
            "怎么用", "怎么设置", "怎么配置", "怎么加", "为什么", "咋", "哪", "填什么", "要填",
        )
        if not any(term in compact for term in permission_terms):
            return False
        return any(term in compact for term in problem_terms)

    async def _maybe_answer_companion_manual_natural_question(self, event: AstrMessageEvent, text: Any) -> bool:
        if not self._is_companion_manual_natural_permission_question(text):
            return False
        question = self._companion_manual_clean_question_text(text, 260)
        answer = await self._companion_manual_answer(event, question)
        await self._reply(event, answer)
        try:
            event.stop_event()
        except Exception:
            pass
        logger.info("自然语言插件权限答疑已接管: text=%s", _single_line(question, 120))
        return True

    def _companion_manual_entry_tags(self, entry: dict[str, Any]) -> set[str]:
        title = re.sub(r"\s+", "", str(entry.get("title") or "")).lower()
        if "被动消息为什么没回" in title or "被动未回复" in title:
            return {"recent"}
        if "连续对话" in title:
            return {"group", "followup"}
        if "高强度" in title:
            return {"group", "high_intensity"}
        if "收口" in title or "防抖" in title:
            return {"debounce"}
        if "唤醒" in title or "答疑误触" in title:
            return {"group"}
        if "休息" in title or "晚安" in title:
            return {"rest"}
        if "智能沉默" in title:
            return {"silence"}
        if "回复复核" in title or "去重" in title or "复读" in title:
            return {"review"}
        if "回复太长" in title or "字数限制" in title:
            return {"style"}
        if "模型" in title or "provider" in title:
            return {"model"}
        if "rememberyou" in title or "联动" in title:
            return {"memory"}
        if "管理命令" in title or "权限" in title or "夹层密码" in title:
            return {"permission"}
        if "主动消息" in title:
            return {"proactive"}
        if "拟人身体" in title or "饥饿" in title:
            return {"state"}
        if "生图" in title or "自拍" in title:
            return {"photo"}
        if "qq空间" in title or "空间" in title:
            return {"qzone"}
        if "群聊老是" in title:
            return {"group"}
        return set()

    def _companion_manual_current_config_value(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return _flat_get(getattr(self, "config", None), key, None)

    def _companion_manual_parse_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否"}:
            return False
        return None

    def _companion_manual_normalize_config_value(self, key: str, value: Any) -> tuple[bool, Any, str]:
        spec = self._companion_manual_config_specs().get(str(key or ""))
        if not isinstance(spec, dict):
            return False, None, f"不允许通过答疑命令修改配置项：{key}"
        kind = str(spec.get("type") or "string")
        try:
            if kind == "bool":
                parsed = self._companion_manual_parse_bool(value)
                if parsed is None:
                    return False, None, "布尔值请使用 开启/关闭、true/false、1/0。"
                return True, parsed, ""
            if kind == "int":
                parsed = int(float(str(value).strip()))
                parsed = max(int(spec.get("min", 0)), min(int(spec.get("max", parsed)), parsed))
                return True, parsed, ""
            if kind == "float":
                parsed = float(str(value).strip())
                parsed = max(float(spec.get("min", 0.0)), min(float(spec.get("max", parsed)), parsed))
                return True, parsed, ""
            if kind == "percent":
                text = str(value or "").strip().replace("%", "")
                parsed = float(text)
                if parsed > 1:
                    parsed = parsed / 100.0
                parsed = max(float(spec.get("min", 0.0)), min(float(spec.get("max", 1.0)), parsed))
                return True, parsed, ""
            if kind == "select":
                text = str(value or "").strip().lower()
                aliases = spec.get("aliases") if isinstance(spec.get("aliases"), dict) else {}
                text = str(aliases.get(text, text))
                choices = spec.get("choices") if isinstance(spec.get("choices"), set) else set()
                if text not in choices:
                    return False, None, f"可选值只有：{', '.join(sorted(str(item) for item in choices))}"
                return True, text, ""
            if kind == "string":
                text = str(value or "").strip()
                max_len = _safe_int(spec.get("max_len"), 1200, 1)
                if len(text) > max_len:
                    text = text[:max_len].strip()
                return True, text, ""
        except (TypeError, ValueError):
            return False, None, f"{self._companion_manual_config_label(key)} 的值格式不对。"
        return False, None, f"不支持的配置类型：{kind}"

    def _companion_manual_values_equal(self, left: Any, right: Any) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return bool(left) == bool(right)
        try:
            return abs(float(left) - float(right)) < 0.0001
        except (TypeError, ValueError):
            return str(left) == str(right)

    def _companion_manual_format_config_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "开启" if value else "关闭"
        if isinstance(value, str) and len(value) > 120:
            return _single_line(value, 120)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _companion_manual_format_config_item_value(self, key: str, value: Any) -> str:
        if str(key or "") in {
            "command_photo_generation_max_daily",
            *PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values(),
        }:
            limit = _safe_int(value, -1, -1, 100)
            if limit < 0:
                return "-1（不限量）"
            if limit == 0:
                return "0（不允许）"
            return f"{limit} 次"
        if str(key or "") in {"rest_reply_probability", "smart_silence_min_confidence"}:
            try:
                number = float(value)
                percent = number * 100 if 0 <= number <= 1 else number
                return f"{percent:.0f}%"
            except (TypeError, ValueError):
                return self._companion_manual_format_config_value(value)
        return self._companion_manual_format_config_value(value)

    def _companion_manual_confidence_label(self, confidence: Any) -> str:
        score = _safe_float(confidence, 0.0, 0.0)
        if score >= 0.78:
            return "高"
        if score >= 0.55:
            return "中"
        return "低"

    def _companion_manual_add_proposal(
        self,
        proposals: list[dict[str, Any]],
        key: str,
        value: Any,
        reason: str,
        *,
        evidence: list[str] | None = None,
        strength: str = "可尝试",
        confidence: float = 0.62,
    ) -> None:
        if any(item.get("key") == key for item in proposals):
            return
        ok, normalized, error = self._companion_manual_normalize_config_value(key, value)
        if not ok:
            logger.debug("答疑可执行建议被跳过: key=%s error=%s", key, _single_line(error, 120))
            return
        old = self._companion_manual_current_config_value(key)
        if self._companion_manual_values_equal(old, normalized):
            return
        evidence_lines = [
            _single_line(item, 150)
            for item in (evidence or [])
            if _single_line(item, 150)
        ]
        current_evidence = f"当前 {key}={self._companion_manual_format_config_item_value(key, old)}"
        if not any(str(item).startswith(f"当前 {key}=") for item in evidence_lines):
            evidence_lines.insert(0, current_evidence)
        proposals.append(
            {
                "key": key,
                "label": self._companion_manual_config_label(key),
                "old": old,
                "value": normalized,
                "reason": _single_line(reason, 160),
                "evidence": evidence_lines[:4],
                "strength": _single_line(strength, 20) or "可尝试",
                "confidence": max(0.0, min(1.0, _safe_float(confidence, 0.0, 0.0))),
            }
        )

    def _companion_manual_build_config_proposals(
        self,
        question: str,
        selected: list[dict[str, Any]],
        event: AstrMessageEvent | None = None,
    ) -> list[dict[str, Any]]:
        query = str(question or "")
        compact = re.sub(r"\s+", "", query).lower()
        titles = " ".join(str(item.get("title") or "") for item in selected)
        titles_compact = re.sub(r"\s+", "", titles).lower()
        primary_title = re.sub(r"\s+", "", str(selected[0].get("title") if selected else "")).lower()
        proposals: list[dict[str, Any]] = []
        runtime = self._companion_manual_runtime_snapshot(event) if event is not None else ""

        def runtime_evidence(*patterns: str) -> list[str]:
            lines = []
            for line in runtime.splitlines():
                if not line:
                    continue
                if any(pattern and pattern in line for pattern in patterns):
                    lines.append(line)
            return lines[:2]

        def current_number(key: str, default: float = 0.0) -> float:
            return _safe_float(self._companion_manual_current_config_value(key), default, 0.0)

        def current_int(key: str, default: int = 0) -> int:
            return _safe_int(self._companion_manual_current_config_value(key), default, 0)

        def current_bool(key: str, default: bool = False) -> bool:
            value = self._companion_manual_current_config_value(key)
            parsed = self._companion_manual_parse_bool(value)
            return bool(default) if parsed is None else parsed

        issue_tags = self._companion_manual_issue_tags(query)
        recent_question = "recent" in issue_tags or any(word in compact for word in ("刚才", "刚刚", "这次", "上一条", "为什么没回", "为什么不回", "没回复", "没发出来"))
        recent_no_reply = self._companion_manual_recent_no_reply_evidence(event, limit=4) if event is not None and recent_question else []
        recent_no_reply_compact = re.sub(r"\s+", "", " ".join(recent_no_reply)).lower()

        group_issue_words = ("群聊不回复", "群里不回复", "群内不回复", "群聊没回复", "群里没回复", "群聊回复慢", "群里回复慢", "好久才回复", "老是不回复")
        group_slow = any(word in compact for word in group_issue_words) or primary_title.startswith("群聊老是不回复")
        followup = any(word in compact for word in ("连续对话", "续接", "接话", "没@", "没at")) or primary_title.startswith("群聊连续对话")
        high_intensity = any(word in compact for word in ("高强度", "收口", "合并", "压制")) or primary_title.startswith("群聊高强度")
        debounce = any(word in compact for word in ("防抖", "智能收口", "补话", "等待", "合并消息")) or primary_title.startswith("消息收口")
        wakeup_mistouch = any(word in compact for word in ("误触", "碰瓷", "插话", "乱回复", "抢话"))
        wakeup_mistouch = wakeup_mistouch or any(word in compact for word in ("太敏感", "过于敏感", "容易触发", "乱触发"))
        photo_mistouch = any(word in compact for word in ("生图误触", "画图误触", "改图误触", "自然语言生图怎么关闭")) or (
            "生图" in compact and any(word in compact for word in ("误触", "太敏感", "过于敏感", "容易触发", "乱触发"))
        )
        qzone_repeat = any(word in compact for word in ("空间重复", "一直回复", "重复回复", "评论重复")) or ("qq空间" in titles_compact and "重复" in compact)
        rest_gate = any(word in compact for word in ("休息闸门", "睡眠闸门", "休息回复", "睡眠回复", "晚安", "睡觉", "睡眠", "不回消息", "不回复消息", "闸门"))
        smart_silence = any(word in compact for word in ("智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话"))
        response_review_issue = any(word in compact for word in ("回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截"))
        verbose_reply = any(word in compact for word in ("话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "回复风格", "口语化"))
        body_state = any(word in compact for word in ("饥饿", "饿", "胃口", "生理期", "来月经", "姨妈", "健康状态", "不适状态", "情绪太低", "情绪过低", "状态太低", "拟人状态"))
        photo_behavior = any(word in compact for word in ("生图没反应", "生图没有反应", "出图后", "好了", "自然语言生图", "自拍", "参考图", "穿搭图", "提示词"))
        qzone_setup = any(word in compact for word in ("空间首次", "第一次使用", "登录空间", "先登录空间", "点赞失效", "空间点赞", "cookie", "onebot")) or ("qq空间" in titles_compact and not qzone_repeat)
        if recent_no_reply_compact:
            focused_tags = issue_tags - {"recent", "location"}

            def recent_focus_allows(tag: str) -> bool:
                return not focused_tags or tag in focused_tags

            if recent_focus_allows("rest") and ("休息闸门" in recent_no_reply_compact or "休息静默" in recent_no_reply_compact):
                rest_gate = True
            if recent_focus_allows("silence") and "智能沉默" in recent_no_reply_compact:
                smart_silence = True
            if recent_focus_allows("group") and "群聊答疑复核" in recent_no_reply_compact:
                wakeup_mistouch = True
            if recent_focus_allows("review") and ("回复复核去重" in recent_no_reply_compact or "发送前去重" in recent_no_reply_compact):
                response_review_issue = True
            if recent_focus_allows("debounce") and ("智能收口" in recent_no_reply_compact or "消息收口" in recent_no_reply_compact):
                debounce = True

        def propose(
            key: str,
            value: Any,
            reason: str,
            scene: str,
            *,
            condition: str = "",
            strength: str = "可尝试",
            confidence: float = 0.62,
            runtime_patterns: tuple[str, ...] = (),
        ) -> None:
            evidence = [f"命中场景：{scene}"]
            if condition:
                evidence.append(condition)
            evidence.extend(runtime_evidence(*runtime_patterns))
            self._companion_manual_add_proposal(
                proposals,
                key,
                value,
                reason,
                evidence=evidence,
                strength=strength,
                confidence=confidence,
            )

        if group_slow or followup:
            if not current_bool("enable_group_conversation_followup", True):
                propose(
                    "enable_group_conversation_followup",
                    True,
                    "开启后，明确叫过 Bot 的同一用户在短窗口内不用每句都 @。",
                    "群聊不回复/连续对话续接",
                    condition="连续对话当前关闭，未 @ 的后续消息更容易断开。",
                    strength="强建议",
                    confidence=0.82,
                    runtime_patterns=("当前群连续对话锚点", "最近群唤醒"),
                )
            seconds = current_int("group_conversation_followup_seconds", 120)
            if seconds < 90 or seconds > 240:
                propose(
                    "group_conversation_followup_seconds",
                    120,
                    "把续接窗口收在 120 秒左右，既不太迟钝，也不容易很久后误认。",
                    "群聊不回复/连续对话续接",
                    condition=f"当前续接窗口 {seconds} 秒不在推荐观察区间 90-240 秒。",
                    strength="强建议" if seconds <= 0 or seconds > 360 else "可尝试",
                    confidence=0.76 if seconds <= 0 or seconds > 360 else 0.66,
                    runtime_patterns=("当前群连续对话锚点", "最近群消息"),
                )
            turns = current_int("group_conversation_followup_max_turns", 1)
            if turns < 1:
                propose(
                    "group_conversation_followup_max_turns",
                    1,
                    "至少允许无 @ 续接一轮，能改善“叫过之后马上不回”的体感。",
                    "群聊不回复/连续对话续接",
                    condition="当前无 @ 续接轮数为 0，明确叫过 Bot 后也不会自然续接。",
                    strength="强建议",
                    confidence=0.8,
                    runtime_patterns=("当前群连续对话锚点",),
                )
            elif group_slow and turns == 1 and "更容易" in compact:
                propose(
                    "group_conversation_followup_max_turns",
                    2,
                    "如果目标是更容易接住同一人的后续补话，可以临时放到 2 轮观察。",
                    "用户明确希望更容易接话",
                    condition="当前最多续接 1 轮，调到 2 会增加对同一用户补话的承接。",
                    strength="可尝试",
                    confidence=0.58,
                    runtime_patterns=("当前群连续对话锚点",),
                )

        if group_slow or high_intensity:
            if not current_bool("enable_group_high_intensity_mode", True):
                propose(
                    "enable_group_high_intensity_mode",
                    True,
                    "开启后连续叫 Bot 会先合并，避免多次 LLM 并发挤爆主链。",
                    "群聊高频唤醒/回复慢",
                    condition="高强度收口当前关闭，连续 @ 时更容易形成多轮并发。",
                    strength="可尝试",
                    confidence=0.6,
                    runtime_patterns=("当前群高强度", "最近群唤醒"),
                )
            if current_int("group_high_intensity_wakeup_threshold", 3) < 4:
                propose(
                    "group_high_intensity_wakeup_threshold",
                    4,
                    "阈值从 3 提到 4，可以减少普通连续对话过早进入高强度压制。",
                    "高强度收口过早/回复慢",
                    condition="当前阈值低于 4，普通连续互动也可能较早进入收口。",
                    strength="可尝试",
                    confidence=0.66,
                    runtime_patterns=("当前群高强度", "最近群唤醒"),
                )
            if current_int("group_high_intensity_cooldown_seconds", 150) > 90:
                propose(
                    "group_high_intensity_cooldown_seconds",
                    90,
                    "收口持续时间缩短到 90 秒，能让群聊更快回到正常续接判断。",
                    "高强度收口持续过久",
                    condition="当前持续时间超过 90 秒，容易让一段时间内的续接判断偏保守。",
                    strength="可尝试",
                    confidence=0.65,
                    runtime_patterns=("当前群高强度",),
                )
            if current_int("group_high_intensity_merge_seconds", 8) > 5:
                propose(
                    "group_high_intensity_merge_seconds",
                    5,
                    "高强度合并等待降到 5 秒，能少一点“好久才回”的体感。",
                    "高强度合并等待偏长",
                    condition="当前合并等待超过 5 秒，会直接增加高强度期间首条回复等待。",
                    strength="可尝试",
                    confidence=0.7,
                    runtime_patterns=("当前群高强度",),
                )
            if str(self._companion_manual_current_config_value("group_high_intensity_merge_scope") or "group") == "group":
                propose(
                    "group_high_intensity_merge_scope",
                    "same_user",
                    "只合并同一发送者的补话，避免别人接话时被全群收口卷进去。",
                    "高强度合并范围过宽",
                    condition="当前按全群合并，其他人接话也可能被并入同一轮。",
                    strength="可尝试",
                    confidence=0.68,
                    runtime_patterns=("最近群消息", "当前群高强度"),
                )

        if group_slow or debounce:
            if current_number("text_message_debounce_seconds", 0.0) > 2:
                propose(
                    "text_message_debounce_seconds",
                    2,
                    "普通文本固定等待降到 2 秒，能减少完整发言后的无谓等待。",
                    "消息收口导致回复慢",
                    condition="当前普通文本固定等待超过 2 秒。",
                    strength="强建议",
                    confidence=0.78,
                    runtime_patterns=("最近智能收口",),
                )
            if current_bool("enable_smart_message_debounce", False) and current_number("smart_message_debounce_wait_seconds", 3.0) > 2:
                propose(
                    "smart_message_debounce_wait_seconds",
                    2,
                    "智能收口的总等待预算降到 2 秒，保留补话感但不拖太久。",
                    "智能收口等待偏长",
                    condition="智能收口已开启，且等待预算超过 2 秒。",
                    strength="可尝试",
                    confidence=0.69,
                    runtime_patterns=("最近智能收口",),
                )
            if current_number("text_message_debounce_max_wait_seconds", 12.0) > 10:
                propose(
                    "text_message_debounce_max_wait_seconds",
                    10,
                    "滑动收口最长等待压到 10 秒，避免用户连续补话时一直拖住回复。",
                    "收口最长等待偏长",
                    condition="当前文本最长等待超过 10 秒。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近智能收口",),
                )

        if wakeup_mistouch and not photo_mistouch and not smart_silence:
            if current_bool("enable_group_wakeup_question", True) and current_int("group_wakeup_question_threshold", 65) < 75:
                propose(
                    "group_wakeup_question_threshold",
                    75,
                    "提高公共求助阈值，能减少普通闲聊被当成“需要 Bot 答疑”。",
                    "群聊答疑/解惑误触",
                    condition="用户问题包含误触/碰瓷/乱插话意图，且当前求助阈值低于 75。",
                    strength="强建议",
                    confidence=0.76,
                    runtime_patterns=("最近群唤醒", "最近被动未回复"),
                )
            if current_number("group_wakeup_short_text_wait_seconds", 15.0) < 5:
                propose(
                    "group_wakeup_short_text_wait_seconds",
                    5,
                    "短唤醒多等几秒补话，能减少一两个字就触发回复。",
                    "短文本唤醒误触",
                    condition="短唤醒补话等待低于 5 秒，碎片消息更容易提前触发。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近群唤醒",),
                )

        if rest_gate:
            if current_bool("enable_rest_reply_simulation", False) and any(word in compact for word in ("误触", "太敏感", "容易", "晚安", "说句晚安", "不回")):
                propose(
                    "enable_rest_reply_simulation",
                    False,
                    "先关闭休息回复闸门，可以避免一句晚安后整段被动消息都被睡眠状态挡掉。",
                    "休息回复闸门误触/晚安后不回",
                    condition="用户问题命中休息/晚安不回，且休息回复闸门当前开启。",
                    strength="强建议",
                    confidence=0.86,
                    runtime_patterns=("最近被动未回复", "休息待补看私聊"),
                )
            elif current_bool("enable_rest_reply_simulation", False) and str(self._companion_manual_current_config_value("rest_reply_mode") or "") == "probability":
                propose(
                    "rest_reply_mode",
                    "llm",
                    "把休息闸门从纯概率切到模型判断，能减少普通晚安被机械挡住。",
                    "休息回复闸门误触",
                    condition="当前是 probability 模式，容易给人随机不回的体感。",
                    strength="可尝试",
                    confidence=0.68,
                    runtime_patterns=("最近被动未回复",),
                )
            if current_bool("enable_rest_reply_simulation", False) and current_int("rest_reply_awake_grace_minutes", 30) < 45:
                propose(
                    "rest_reply_awake_grace_minutes",
                    60,
                    "清醒宽限调到 60 分钟，刚被叫醒后的一小段对话不容易再次被当作睡眠中。",
                    "休息回复闸门反复拦截",
                    condition="当前清醒宽限低于 45 分钟。",
                    strength="可尝试",
                    confidence=0.63,
                    runtime_patterns=("最近被动未回复", "休息待补看私聊"),
                )

        if smart_silence:
            if current_bool("enable_smart_silence", True) and any(word in compact for word in ("误触", "太敏感", "不该沉默", "没回", "不回")):
                threshold = current_number("smart_silence_min_confidence", 0.66)
                if threshold < 0.76:
                    propose(
                        "smart_silence_min_confidence",
                        0.78,
                        "提高沉默置信度，只有更确定是用户想结束话题时才取消回复。",
                        "智能沉默误触",
                        condition=f"当前智能沉默置信度 {threshold:.2f} 低于 0.76。",
                        strength="强建议",
                        confidence=0.78,
                        runtime_patterns=("最近被动未回复",),
                    )
                else:
                    propose(
                        "enable_smart_silence",
                        False,
                        "先关掉智能沉默止血，确认误触样本后再重新打开调阈值。",
                        "智能沉默误触",
                        condition="用户明确反馈沉默误触，且阈值已经不低。",
                        strength="可尝试",
                        confidence=0.61,
                        runtime_patterns=("最近被动未回复",),
                    )

        if response_review_issue:
            if current_bool("enable_passive_response_review", True) and any(word in compact for word in ("关闭", "关掉", "不要", "先关")):
                propose(
                    "enable_passive_response_review",
                    False,
                    "先关闭被动回复复核可以止血，主动消息终审不会受影响。",
                    "用户明确要求关闭被动复核",
                    condition="问题里明确出现关闭/不要复核，且被动复核当前开启。",
                    strength="可尝试",
                    confidence=0.72,
                    runtime_patterns=("最近被动未回复",),
                )
            mode = str(self._companion_manual_current_config_value("passive_review_mode") or "severe_only").strip()
            if current_bool("enable_passive_response_review", True) and mode == "full":
                propose(
                    "passive_review_mode",
                    "severe_only",
                    "从 full 调回 severe_only，可以保留严重问题保护，同时减少普通被动回复被过度改写或误拦截。",
                    "回复复核过强/误杀",
                    condition="当前复核模式是 full，普通短回复也更容易进入模型复核。",
                    strength="强建议",
                    confidence=0.76,
                    runtime_patterns=("最近被动未回复",),
                )
            if current_bool("enable_passive_response_review", True) and current_int("response_review_max_chars", 260) < 220:
                propose(
                    "response_review_max_chars",
                    260,
                    "把被动复核长度阈值调回 260 字附近，避免很短的正常闲聊频繁进入复核链。",
                    "被动复核长度阈值偏低",
                    condition="当前被动复核长度阈值低于 220 字。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近被动未回复",),
                )

        if verbose_reply:
            style_text = str(self._companion_manual_current_config_value("reply_style_prompt") or "").strip()
            concise_style = (
                "每次回复至多三句话；简单回答尽量保持在 1-2 句，口语化、简洁，跟随当前对话节奏；"
                "必须使用简体中文，符合社交媒体聊天习惯。需要排障、教程、复杂说明或用户明确要求详细解释时，可以优先保证信息完整。"
            )
            if "至多三句" not in style_text and "至多三句话" not in style_text and "1-2" not in style_text:
                propose(
                    "reply_style_prompt",
                    concise_style,
                    "把简洁、口语化和复杂问题例外写进统一回复风格，能压住高强度群聊里动态提示词带来的话痨倾向。",
                    "回复太长/回复风格不生效",
                    condition="当前回复风格没有检测到明确的句数/简洁约束。",
                    strength="强建议",
                    confidence=0.79,
                )

        if photo_mistouch:
            if str(self._companion_manual_current_config_value("natural_language_photo_generation_mode") or "tool_first").strip().lower() == "rule_fast":
                propose(
                    "natural_language_photo_generation_mode",
                    "tool_first",
                    "把非指令生图改回工具优先，让普通聊天先进入主链，只有模型明确调用 pc_generate_photo 时才生图，可减少和闲聊或其他生图插件抢触发。",
                    "非指令生图误触",
                    condition="用户问题明确提到生图/改图误触，且当前使用规则快判前置接管。",
                    strength="强建议",
                    confidence=0.86,
                )
            if current_bool("enable_natural_language_photo_generation", False):
                propose(
                    "enable_natural_language_photo_generation",
                    False,
                    "关闭规则快判后，插件不会在主链前直接抢高置信生图请求；显式指令和 pc_generate_photo 工具仍可正常使用。",
                    "规则快判生图误触",
                    condition="用户问题明确提到生图/改图误触，且规则快判入口当前开启。",
                    strength="可尝试",
                    confidence=0.78,
                )

        if photo_behavior:
            command_quota_question = any(word in compact for word in ("上限", "额度")) and any(
                marker in compact for marker in ("用户请求", "指令生图", "陪伴生图", "陪伴自拍", "陪伴改图", "额度用完")
            )
            command_photo_limit = _safe_int(
                self._companion_manual_current_config_value("command_photo_generation_max_daily"),
                -1,
                -1,
                100,
            )
            if command_quota_question and command_photo_limit >= 0:
                propose(
                    "command_photo_generation_max_daily",
                    -1,
                    "把用户请求生图每日上限设为 -1，显式陪伴生图指令与 pc_generate_photo 工具调用都不再受每日次数限制；0 表示完全不允许用户请求生图。",
                    "用户请求生图上限",
                    condition="用户明确询问指令/工具生图额度，并希望取消每日限制。",
                    strength="可尝试",
                    confidence=0.86,
                )
            rule_quota_question = "上限" in compact and any(
                marker in compact for marker in ("规则快判", "自然语言生图", "非指令生图", "rule_fast")
            )
            if rule_quota_question and current_int("natural_language_photo_generation_max_daily", 2) < 100:
                propose(
                    "natural_language_photo_generation_max_daily",
                    100,
                    "把规则快判每日上限放宽到 100，适合测试期观察插件前置接管和出图链路。",
                    "规则快判生图上限",
                    condition="用户问题提到规则快判/自然语言生图上限，且当前上限低于 100。",
                    strength="可尝试",
                    confidence=0.74,
                )
            current_mode = str(self._companion_manual_current_config_value("natural_language_photo_generation_mode") or "tool_first").strip().lower()
            if any(word in compact for word in ("呆", "好了", "没反应", "没有反应")) and current_mode == "off":
                propose(
                    "natural_language_photo_generation_mode",
                    "tool_first",
                    "如果希望普通聊天里说“画一张/发自拍”能触发生图，先用工具优先：主链理解意图后调用 pc_generate_photo，不会像规则快判那样抢普通对话。",
                    "非指令生图没有反应",
                    condition="当前非指令生图处理方式为 off。",
                    strength="可尝试",
                    confidence=0.72,
                )

        if qzone_repeat:
            if current_bool("enable_qzone_comment_inbox", False):
                propose(
                    "enable_qzone_comment_inbox",
                    False,
                    "先暂停评论收件箱，避免排障前继续对同一条评论公开回复。",
                    "QQ 空间评论重复回复",
                    condition="用户问题明确提到空间评论重复/一直回复，先关入口可止血。",
                    strength="强建议",
                    confidence=0.83,
                )
            if current_int("qzone_comment_inbox_interval_minutes", 60) < 60:
                propose(
                    "qzone_comment_inbox_interval_minutes",
                    60,
                    "评论检查间隔至少 60 分钟，降低重复扫描带来的二次回复风险。",
                    "QQ 空间评论重复回复",
                    condition="当前评论检查间隔小于 60 分钟，重复扫描频率偏高。",
                    strength="可尝试",
                    confidence=0.62,
                )
            if current_int("qzone_comment_inbox_max_replies_per_tick", 1) > 1:
                propose(
                    "qzone_comment_inbox_max_replies_per_tick",
                    1,
                    "每轮最多回复 1 条，排障时更容易定位是哪条评论触发。",
                    "QQ 空间评论重复回复",
                    condition="当前每轮可回复多条，排障时不容易定位触发源。",
                    strength="可尝试",
                    confidence=0.6,
                )

        if qzone_setup and current_bool("enable_qzone_comment_inbox", False) and current_int("qzone_comment_inbox_interval_minutes", 60) < 30:
            propose(
                "qzone_comment_inbox_interval_minutes",
                30,
                "首次接入空间时先把评论轮询间隔放到 30 分钟以上，更容易观察 Cookie 和去重是否稳定。",
                "QQ 空间首次使用/点赞评论排障",
                condition="评论收件箱已开启，且当前轮询间隔偏短。",
                strength="可尝试",
                confidence=0.58,
                runtime_patterns=("最近排障测试",),
            )

        if body_state:
            if any(word in compact for word in ("一天都是饥饿", "总是饥饿", "一直饿", "老是饿", "一直饥饿")) and current_bool("enable_hunger_state", True):
                propose(
                    "enable_hunger_state",
                    False,
                    "先关闭饥饿状态止血，避免状态机持续把吃饭/胃口写进回复。",
                    "饥饿状态长期不退",
                    condition="用户反馈一天都是饥饿状态，且饥饿状态当前开启。",
                    strength="强建议",
                    confidence=0.82,
                    runtime_patterns=("当前用户状态",),
                )
            if any(word in compact for word in ("生理期", "来月经", "姨妈")) and any(word in compact for word in ("不要", "关闭", "不想", "误触", "没设置", "奇怪")) and current_bool("enable_cycle_state", True):
                propose(
                    "enable_cycle_state",
                    False,
                    "关闭后会清理生理期相关状态，避免未确认用户接受时继续注入。",
                    "生理期模拟不想启用",
                    condition="用户问题包含生理期模拟负反馈，且开关当前开启。",
                    strength="强建议",
                    confidence=0.8,
                    runtime_patterns=("最近被动未回复",),
                )
            if any(word in compact for word in ("情绪太低", "情绪过低", "状态太低", "太容易过低")) and current_int("humanized_state_intensity", 50) > 35:
                propose(
                    "humanized_state_intensity",
                    35,
                    "降低拟人状态强度，能让健康、饥饿、情绪余波这类状态少一点压过人格。",
                    "拟人状态过强",
                    condition="用户反馈情绪/状态过低，且当前状态强度高于 35。",
                    strength="可尝试",
                    confidence=0.69,
                )

        primary_tags = issue_tags

        def proposal_rank(item: dict[str, Any]) -> float:
            key = str(item.get("key") or "")
            score = _safe_float(item.get("confidence"), 0.0, 0.0) * 100
            if str(item.get("strength") or "") == "强建议":
                score += 18
            if recent_no_reply_compact and any(pattern in recent_no_reply_compact for pattern in ("休息闸门", "智能沉默", "回复复核", "群聊答疑复核")):
                if (
                    ("休息闸门" in recent_no_reply_compact and (key.startswith("rest_") or key == "enable_rest_reply_simulation"))
                    or ("智能沉默" in recent_no_reply_compact and (key.startswith("smart_silence") or key == "enable_smart_silence"))
                    or ("回复复核" in recent_no_reply_compact and key in {"enable_passive_response_review", "passive_review_mode", "passive_review_strength", "response_review_max_chars"})
                    or ("群聊答疑复核" in recent_no_reply_compact and key in {"group_wakeup_question_threshold", "enable_group_wakeup_question"})
                ):
                    score += 22
            if "rest" in primary_tags and (key.startswith("rest_") or key in {"enable_rest_reply_simulation", "enable_rest_backlog_reply"}):
                score += 16
            if "silence" in primary_tags and (key.startswith("smart_silence") or key == "enable_smart_silence"):
                score += 16
            if "review" in primary_tags and key in {"enable_passive_response_review", "passive_review_mode", "passive_review_strength", "response_review_max_chars"}:
                score += 16
            if "photo" in primary_tags and ("photo" in key or "image" in key):
                score += 16
            if "state" in primary_tags and key in {"enable_health_state", "enable_hunger_state", "enable_cycle_state", "humanized_state_intensity"}:
                score += 16
            if "style" in primary_tags and key == "reply_style_prompt":
                score += 16
            if "qzone" in primary_tags and (key.startswith("qzone_") or key == "enable_qzone_comment_inbox"):
                score += 16
            return score

        proposals.sort(key=proposal_rank, reverse=True)
        return proposals[:6]
