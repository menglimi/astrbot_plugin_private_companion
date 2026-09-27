# -*- coding: utf-8 -*-
"""ProactiveMessageSendReviewPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_send_review.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 462 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageSendReviewMixin）。
"""
from __future__ import annotations

from .proactive_message_send_review_shared import _now_ts
from .proactive_message_send_review_shared import Any
from .proactive_message_send_review_shared import _safe_float
from .proactive_message_send_review_shared import _safe_int
from .proactive_message_send_review_shared import _single_line
from .proactive_message_send_review_shared import _today_key
from .proactive_message_send_review_shared import datetime
from .proactive_message_send_review_shared import re
from .proactive_message_send_review_shared import runtime_persona_setting



class ProactiveMessageSendReviewPart01Mixin:
    """ProactiveMessageSendReviewPart01Mixin（从 ProactiveMessageSendReviewMixin 拆出）。"""


    def _local_proactive_send_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        motive: str = "",
        topic: str = "",
        action_context: str = "",
    ) -> dict[str, Any]:
        strength = self._proactive_review_strength()
        cleaned = _single_line(text, 500)
        if not cleaned:
            return {"decision": "drop", "reason": "主动消息为空", "hard": True}
        external_info_reasons = {"bili_video_share", "news_share", "web_exploration_share"}
        external_share_active = reason in external_info_reasons
        link_platform_mismatch = self._proactive_link_platform_mismatch_reason(cleaned)
        if link_platform_mismatch:
            if external_share_active:
                external_fix = self._external_share_source_consistency_decision(
                    user,
                    cleaned,
                    reason=reason,
                    topic=topic,
                    motive=motive,
                    action_context=action_context,
                )
                if external_fix:
                    return external_fix
            return {
                "decision": "drop",
                "reason": link_platform_mismatch,
                "hard": True,
            }
        if reason == "environment_change" and re.search(
            r"https?://|(?:^|[^A-Za-z0-9])BV[0-9A-Za-z]{8,16}(?:$|[^A-Za-z0-9])|《[^》\n]{1,120}》",
            cleaned,
            flags=re.I,
        ):
            return {
                "decision": "drop",
                "reason": "环境变化主动消息混入了文章、视频或旧链接来源",
                "hard": True,
            }
        wrong_address = self._wrong_proactive_recipient_address(
            cleaned,
            user,
            _single_line(user.get("nickname"), 40),
        )
        if wrong_address:
            repaired_text, repaired_address = self._repair_proactive_recipient_address(
                cleaned,
                user,
                _single_line(user.get("nickname"), 40),
            )
            if repaired_address:
                return {
                    "decision": "rewrite",
                    "reason": f"已把收件人称呼纠正为当前昵称：{repaired_address}",
                    "text": repaired_text,
                    "hard": True,
                }
            return {
                "decision": "drop",
                "reason": f"主动正文无法确认收件人称呼：{wrong_address}",
                "hard": True,
            }
        outbound_guard = self._validate_proactive_outbound_candidate(
            cleaned,
            reason=reason,
            action=action,
            source="review",
        )
        guard_decision = str(outbound_guard.get("decision") or "send")
        if guard_decision == "drop":
            return {
                "decision": "drop",
                "reason": _single_line(outbound_guard.get("reason"), 120) or "主动候选疑似内部泄漏",
                "hard": bool(outbound_guard.get("hard", True)),
            }
        if guard_decision == "rewrite":
            rewritten_guard_text = _single_line(outbound_guard.get("text"), 500)
            if rewritten_guard_text:
                return {
                    "decision": "rewrite",
                    "reason": _single_line(outbound_guard.get("reason"), 120) or "清理主动候选内部残留",
                    "text": rewritten_guard_text,
                }
            return {"decision": "drop", "reason": _single_line(outbound_guard.get("reason"), 120) or "主动候选只剩内部残留", "hard": True}
        fact_decision = self._unverified_proactive_fact_decision(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if fact_decision:
            return fact_decision
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception:
                semantics = {}
        semantic_kind = _single_line(semantics.get("kind"), 40)
        semantic_anchor_type = _single_line(semantics.get("anchor_type"), 40)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        default_hard_risk = 0.70 if strength == "lenient" else 0.45
        hard_risk_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(
                        self, "proactive_review_hard_risk_threshold", default_hard_risk
                    ),
                    default_hard_risk,
                ),
            ),
        )
        low_score_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "proactive_review_low_score_threshold", 0.34),
                    0.34,
                ),
            ),
        )
        pressure_threshold = max(
            0.0,
            min(
                1.0,
                _safe_float(
                    runtime_persona_setting(self, "proactive_review_pressure_threshold", 0.55),
                    0.55,
                ),
            ),
        )
        if semantic_risk >= hard_risk_threshold:
            return {
                "decision": "drop",
                "reason": f"候选语义风险偏高 risk={semantic_risk:.2f}/{hard_risk_threshold:.2f}",
                "hard": True,
            }
        if strength != "lenient" and semantic_score < low_score_threshold and semantic_pressure >= pressure_threshold:
            return {"decision": "defer", "reason": "候选由头偏虚且打扰压力高", "delay_minutes": 75}
        reply_like_openers = (
            "好呀", "好啊", "可以呀", "行啊", "那就", "你说呢", "要不", "刚看到", "才看到",
            "你来了", "你叫我", "你问", "我帮你查", "我去问", "我去说",
        )
        matched_reply_opener = next((token for token in reply_like_openers if cleaned.startswith(token)), "")
        if matched_reply_opener and not (external_share_active and matched_reply_opener in {"刚看到", "才看到"}):
            if strength != "strict":
                rewritten = re.sub(
                    r"^(?:好呀|好啊|可以呀|行啊|那就|你说呢|要不|刚看到|才看到|你来了|你叫我|你问|我帮你查|我去问|我去说)[，,。！!？?\s]*",
                    "",
                    cleaned,
                    count=1,
                ).strip()
                if rewritten and len(rewritten) >= 4:
                    return {"decision": "rewrite", "reason": "去掉回复式开头", "text": rewritten}
            return {"decision": "drop", "reason": "像是在回复刚发来的消息"}
        motive_leak_repaired = self._strip_proactive_motive_leak_text(cleaned)
        if motive_leak_repaired != cleaned:
            if motive_leak_repaired and len(motive_leak_repaired) >= 2:
                return {"decision": "rewrite", "reason": "去掉主动动机自述", "text": motive_leak_repaired}
            return {"decision": "defer", "reason": "主动消息只剩动机自述", "delay_minutes": 75}
        vague = ("想你了", "来看看你", "你在忙什么", "最近怎么样", "吃了吗", "辛苦了", "在吗", "忙不忙")
        if strength != "lenient" and reason in {"check_in", "quiet_care", "state_share"} and any(token in cleaned for token in vague):
            return {"decision": "defer", "reason": "普通主动过于泛泛", "delay_minutes": 60}
        if strength != "lenient" and semantic_kind in {"self_share", "external_share", "observation"} and any(token in cleaned for token in vague):
            return {"decision": "defer", "reason": "生成结果偏离分享型由头", "delay_minutes": 60}
        if external_share_active:
            external_fix = self._external_share_source_consistency_decision(
                user,
                cleaned,
                reason=reason,
                topic=topic,
                motive=motive,
                action_context=action_context,
            )
            if external_fix:
                return external_fix
        role = self._private_user_role(user) if isinstance(user, dict) else "friend"
        # 外部分享（新闻/B站/搜索）跳过「疑似混入其他私聊互动」检查（与 PR #168 同源）：
        # 该检查的 _daily_plan_clause_has_named_message_interaction 正则会把新闻正文
        # 「看到个消息」的「个」误判为私聊 target（08-25 16:19 红色沙漠新闻被此误杀实锤）。
        # 外部分享 gen 不含对其他用户的私聊互动描述，跳过不会漏真问题。
        if role == "owner" and not external_share_active:
            social_checker = getattr(self, "_daily_plan_clause_has_named_message_interaction", None)
            has_cross_private_interaction = False
            if callable(social_checker):
                try:
                    has_cross_private_interaction = bool(social_checker(cleaned))
                except Exception:
                    has_cross_private_interaction = False
            if has_cross_private_interaction or any(token in cleaned for token in ("朋友那边", "朋友用户", "朋友私聊", "次要用户那边", "次要用户私聊")):
                return {"decision": "drop", "reason": "疑似混入其他私聊互动", "hard": True}
        if _safe_int(user.get("ignored_streak"), 0, 0) >= 1 and cleaned.count("？") + cleaned.count("?") >= 2:
            return {"decision": "rewrite", "reason": "未回应状态下问题太多", "text": re.split(r"[？?]", cleaned, maxsplit=1)[0].rstrip("，,。") + "。"}
        return {"decision": "send", "reason": "本地检查通过"}

    def _strip_proactive_motive_leak_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units: list[str] = []
        for line in cleaned.splitlines() or [cleaned]:
            units.extend(self._split_proactive_sentence_units(line))
        if not units:
            units = [cleaned]

        leak_unit_patterns = (
            r"(?:怕|担心)[^。！？\n]{0,16}(?:太早|太晚|打扰|吵到|烦到)",
            r"(?:先|又|就)?(?:收住|忍住|憋住|忍了一下|放了一会)[^。！？\n]{0,20}",
            r"(?:结果|后来)?[^。！？\n]{0,12}(?:绕了一圈|转了一圈|想了半天)[^。！？\n]{0,24}(?:来找你|找你|说出口)",
            r"(?:还是|又|最后|结果)[^。！？\n]{0,12}(?:来找你|找你|跑来找你|过来找你)[啦了啊呀]*",
            r"(?:没什么事|没有别的事|也没什么)[^。！？\n]{0,18}(?:就是|只是)?想(?:来)?(?:找你|跟你说话|和你说话|说一句)",
        )
        leak_clause_patterns = (
            r"[，,、\s]*(?:刚[^，。！？\n]{0,24})?(?:就|还是)?想(?:先)?(?:跟|和)?你(?:说早安|说早|说一句|说点什么|打个招呼|聊两句|说话)[^，。！？\n]*",
            r"[，,、\s]*(?:中午|晚上|早上|这会儿|刚才|刚刚)?[^，。！？\n]{0,18}(?:就|又|还是)?想(?:顺手)?(?:来)?(?:找你|跟你打个照面|和你打个照面|往你这边冒个头)[^，。！？\n]*",
            r"[，,、\s]*(?:刚刚|刚才|这会儿|今天|明明|还是)?[^，。！？\n]{0,24}想(?:和|给|问|提醒|确认|看看)?用户[^，。！？\n]*",
            r"[，,、\s]*(?:怕|担心)[^，。！？\n]{0,16}(?:太早|太晚|打扰|吵到|烦到)[^，。！？\n]*",
            r"[，,、\s]*(?:就)?先(?:收住|忍住|憋住)[^，。！？\n]*",
            r"[，,、\s]*(?:结果|后来)?[^，。！？\n]{0,12}(?:绕了一圈|转了一圈|想了半天)[^，。！？\n]*",
            r"[，,、\s]*莫名觉得[^，。！？\n]*",
            r"[，,、\s]*(?:顺手)(?:丢给你|放这儿|递给你|想起|分享一下|分享一下)[^，。！？\n]*",
            r"[，,、\s]*(?:多看一眼|也会留意这个|也会看一眼)[^，。！？\n]*",
            r"[，,、\s]*(?:只)?轻轻(?:提一句|提醒[^，。！？\n]*|说声|补上一句)[^，。！？\n]*",
            r"[，,、\s]*想(?:短短|轻轻)(?:说一句|提一句|说句话|提一声|说一下|打声招呼)[^，。！？\n]*",
            r"[，,、\s]*感觉和[^，。！？\n]{0,20}有点贴[^，。！？\n]*",
            r"[，,、\s]*想跟你说一句[^，。！？\n]*",
        )
        kept: list[str] = []
        changed = False
        for raw_unit in units:
            unit = str(raw_unit or "").strip()
            if not unit:
                continue
            if any(re.search(pattern, unit) for pattern in leak_unit_patterns):
                changed = True
                continue
            repaired = unit
            for pattern in leak_clause_patterns:
                repaired, count = re.subn(pattern, "", repaired)
                changed = changed or count > 0
            repaired = repaired.strip(" ，,、。！？!?；;")
            if repaired:
                kept.append(self._ensure_chat_sentence_punctuation(repaired))
            elif repaired != unit:
                changed = True
        if not changed:
            return cleaned
        return "\n".join(kept)[:260].strip()

    def _proactive_review_strength(self) -> str:
        strength = str(
            runtime_persona_setting(self, "proactive_review_strength", "lenient") or "lenient"
        ).strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    def _effective_proactive_review_mode(self) -> str:
        mode = str(
            runtime_persona_setting(self, "proactive_review_mode", "full") or "full"
        ).strip().lower()
        return mode if mode in {"local_only", "severe_only", "full"} else "full"

    @staticmethod
    def _proactive_review_hard_block_reason(reason: str) -> bool:
        text = str(reason or "")
        if not text:
            return False
        markers = (
            "隐私", "泄露", "越界", "风险", "危险", "敏感", "违规", "骚扰", "威胁",
            "其他私聊", "朋友私聊", "混入", "承诺工具", "承诺发图", "承诺语音", "承诺查询",
            "系统动作", "发送状态", "状态汇报", "工具执行", "工具结果", "工具回执",
            "执行回执", "发送回执", "系统回执", "不是角色真正",
        )
        return any(marker in text for marker in markers)

    def _balanced_proactive_defer_release_reason(
        self,
        user: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> str:
        if not isinstance(user, dict):
            return ""
        note_text = _single_line(note, 120)
        generic_defer = any(
            token in note_text
            for token in ("刚结束", "稍后", "稍候", "时机", "自然", "突兀", "间隔", "不合适")
        )
        if not generic_defer:
            return ""
        today = _today_key()
        sent_today = _safe_int(user.get("sent_today"), 0) if str(user.get("sent_day") or "") == today else 0
        if sent_today > 0:
            return ""
        last_sent_at = max(
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        if last_sent_at > 0 and datetime.fromtimestamp(last_sent_at).strftime("%Y-%m-%d") == today:
            return ""
        check_now = _now_ts() if now is None else now
        now_dt = datetime.fromtimestamp(check_now)
        if now_dt.hour * 60 + now_dt.minute < 10 * 60 + 30:
            return ""
        idle_getter = getattr(self, "_effective_user_idle_minutes", None)
        try:
            idle_minutes = (
                idle_getter(user)
                if callable(idle_getter)
                else _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
            )
        except Exception:
            idle_minutes = _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < max(10, min(60, idle_minutes)) * 60:
            return ""
        return "今日尚无主动且候选非硬风险，标准强度低频放行"

    @staticmethod
    def _proactive_review_elapsed_text(seconds: float) -> str:
        if seconds < 0:
            return "未知"
        if seconds < 90:
            return "刚刚"
        if seconds < 3600:
            return f"约{max(1, int(seconds // 60))}分钟"
        if seconds < 86400:
            return f"约{max(1, int(seconds // 3600))}小时"
        return f"约{max(1, int(seconds // 86400))}天"

    @staticmethod
    def _proactive_has_verified_recent_fact_source(
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> bool:
        source_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
            "weather_alert",
            "goodnight_screen_check",
        }
        if str(reason or "").strip() in source_reasons:
            return True
        context = str(action_context or "")
        if str(reason or "").strip() == "group_share" and "群聊分享线索" in context:
            return True
        if re.search(r"(?:真实图片文件|图片路径|真实动作结果|工具结果|来源链接|https?://)", context, re.I):
            return True
        return str(action or "message").strip() not in {"", "message", "photo_text"} and bool(_single_line(context, 240))

    def _unverified_proactive_fact_decision(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> dict[str, Any] | None:
        if self._proactive_has_verified_recent_fact_source(
            reason=reason,
            action=action,
            action_context=action_context,
        ):
            return None
        recent_self_action = re.compile(
            r"(?:我\s*)?(?:刚刚|刚才|方才|刚|才)\s*"
            r"(?:刷到|刷了|看到|看见|听到|听见|读到|发现|碰到|遇到|收到|"
            r"买了|拍了|做了|画了|写了|吃了|喝了|回到|到家|出门|回来)"
        )
        stale_meal_attribution = re.compile(
            r"你[^。！？!?；;…~～]{0,12}(?:昨天|昨晚)[^。！？!?；;…~～]{0,16}"
            r"(?:吃的|点的|喝的|吃了|点了|喝了)"
        )
        unsafe_units: list[str] = []
        safe_units: list[str] = []
        for unit in self._split_proactive_sentence_units(text):
            recent_claim = bool(recent_self_action.search(unit))
            stale_claim = reason in {"meal_care", "meal_care_followup"} and bool(stale_meal_attribution.search(unit))
            if recent_claim or stale_claim:
                unsafe_units.append(unit)
            else:
                safe_units.append(unit)
        if not unsafe_units:
            return None
        repaired = " ".join(safe_units).strip()
        if repaired and len(re.sub(r"\s+", "", repaired)) >= 4:
            return {
                "decision": "rewrite",
                "reason": "已移除无真实来源的近期动作或旧饮食归因",
                "text": repaired,
                "hard": True,
            }
        return {
            "decision": "drop",
            "reason": "主动正文依赖无真实来源的近期动作或旧饮食归因",
            "hard": True,
        }

    def _format_proactive_review_runtime_context(self, user: dict[str, Any], *, now: float | None = None) -> str:
        check_now = _now_ts() if now is None else now
        now_dt = datetime.fromtimestamp(check_now)
        today = _today_key()
        sent_today = _safe_int(user.get("sent_today"), 0) if str(user.get("sent_day") or "") == today else 0
        last_sent_at = max(
            _safe_float(user.get("last_proactive_sent_at"), 0),
            _safe_float(user.get("last_sent"), 0),
        )
        activity_getter = getattr(self, "_latest_private_user_activity_ts", None)
        try:
            last_private_at = activity_getter(user) if callable(activity_getter) else 0
        except Exception:
            last_private_at = 0
        if last_private_at <= 0:
            last_private_at = max(
                _safe_float(user.get("last_user_message_at"), 0),
                _safe_float(user.get("last_private_seen"), 0),
            )
        idle_getter = getattr(self, "_effective_user_idle_minutes", None)
        interval_getter = getattr(self, "_effective_user_min_interval_minutes", None)
        try:
            idle_minutes = (
                idle_getter(user)
                if callable(idle_getter)
                else _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
            )
        except Exception:
            idle_minutes = _safe_int(runtime_persona_setting(self, "idle_minutes", 20), 20)
        try:
            min_interval = (
                interval_getter(user)
                if callable(interval_getter)
                else _safe_int(runtime_persona_setting(self, "min_interval_minutes", 80), 80)
            )
        except Exception:
            min_interval = _safe_int(
                runtime_persona_setting(self, "min_interval_minutes", 80), 80
            )
        private_elapsed = check_now - last_private_at if last_private_at > 0 else -1
        sent_elapsed = check_now - last_sent_at if last_sent_at > 0 else -1
        runtime_context = "\n".join(
            part
            for part in (
                f"当前判定时间：{now_dt.strftime('%Y-%m-%d %H:%M')}",
                f"今天已成功主动：{sent_today} 条",
                f"距用户上次私聊活动：{self._proactive_review_elapsed_text(private_elapsed)}；普通主动要求空闲约 {max(0, int(idle_minutes))} 分钟",
                f"距上次主动发送：{self._proactive_review_elapsed_text(sent_elapsed)}；普通主动最小间隔约 {max(0, int(min_interval))} 分钟",
                f"上次主动内容：{_single_line(user.get('last_proactive_message'), 120)}" if user.get("last_proactive_message") else "",
            )
            if part
        )
        calendar_hint = self._format_proactive_calendar_constraint_hint()
        return f"{runtime_context}\n{calendar_hint}".strip() if calendar_hint else runtime_context
