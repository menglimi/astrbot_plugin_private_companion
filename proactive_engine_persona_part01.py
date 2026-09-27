# -*- coding: utf-8 -*-
"""ProactiveEnginePersonaPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_persona.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 468 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEnginePersonaMixin）。
"""
from __future__ import annotations
from .proactive_engine_persona_shared import Any
from .proactive_engine_persona_shared import _engine_host
from .proactive_engine_persona_shared import _safe_float
from .proactive_engine_persona_shared import _safe_int
from .proactive_engine_persona_shared import _single_line
from .proactive_engine_persona_shared import _today_key
from .proactive_engine_persona_shared import hashlib
from .proactive_engine_persona_shared import runtime_persona_setting



class ProactiveEnginePersonaPart01Mixin:
    """ProactiveEnginePersonaPart01Mixin（从 ProactiveEnginePersonaMixin 拆出）。"""


    def _proactive_source_feedback_modifier(self, user: dict[str, Any], source: str) -> float:
        """Bias candidates using reply quality for their own source, not global silence."""
        normalized = _single_line(source, 40) or "unknown"
        feedback = user.get("proactive_source_feedback") if isinstance(user, dict) else None
        bucket = feedback.get(normalized) if isinstance(feedback, dict) else None
        if not isinstance(bucket, dict):
            return 0.0
        sent = _safe_float(bucket.get("weighted_sent"), _safe_float(bucket.get("sent"), 0.0), 0.0)
        if sent < 2.0:
            return 0.0
        replied = min(sent, _safe_float(bucket.get("weighted_replied"), _safe_float(bucket.get("replied"), 0.0), 0.0))
        positive = min(replied, _safe_float(bucket.get("weighted_positive"), _safe_float(bucket.get("positive"), 0.0), 0.0))
        negative = min(replied, _safe_float(bucket.get("weighted_negative"), _safe_float(bucket.get("negative"), 0.0), 0.0))
        reply_rate = replied / max(1, sent)
        feedback_rate = (positive - negative) / max(1, sent)
        # Keep the learnt effect bounded; the route and hard gates remain authoritative.
        return max(-0.18, min(0.18, (reply_rate - 0.35) * 0.24 + feedback_rate * 0.08))

    def _proactive_persona_alignment(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        role = self._private_user_role(user)
        normalized_reason = str(reason or "check_in")
        normalized_action = str(action or "message").strip() or "message"
        normalized_motive = self._normalize_internal_motive_text(_single_line(motive, 180))
        normalized_topic = _single_line(topic, 80)
        normalized_source = _single_line(source, 40)
        text = f"{normalized_reason} {normalized_action} {normalized_topic} {normalized_motive}"
        profile = self._persona_action_profile()
        score = 0.66
        notes: list[str] = []
        blocker = False

        def note(text_value: str) -> None:
            clean = _single_line(text_value, 60)
            if clean and clean not in notes:
                notes.append(clean)

        intimate = (
            self._proactive_reason_is_intimate(normalized_reason)
            or self._proactive_action_is_intimate(normalized_action)
            or self._proactive_text_is_intimate(normalized_reason, normalized_action, normalized_motive, normalized_topic)
        )
        if role == "friend":
            score += 0.02
            if self._friend_sensitive_proactive_reason(normalized_reason) or self._friend_sensitive_proactive_action(normalized_action):
                blocker = True
                score -= 0.45
                note("次要用户关系不适合这个主动来源/能力")
            if intimate:
                score -= 0.22
                note("次要用户关系下亲密度偏高")
            if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
                score -= 0.12
                note("次要用户关系下动机太像索取回应")
        else:
            if normalized_reason == "special_day_greeting":
                ritual_markers = ("节日", "仪式", "纪念", "浪漫", "庆祝", "情人", "七夕")
                if any(marker in str(self._get_default_persona_prompt() or "") for marker in ritual_markers):
                    score += 0.07
                    note("人格对节日/纪念性表达有承载空间")
                else:
                    score -= 0.04
                    note("人格不偏节日仪式，表达应收成平常口吻")
            elif normalized_reason == "insomnia_night" and not (profile.get("clingy") or profile.get("voicey")):
                score -= 0.03
                note("人格主动温度偏低，失眠关怀只留很短一句")
            if intimate and (profile.get("clingy") or profile.get("voicey")):
                score += 0.07
                note("亲近型人格可承载这个主动")
            if self._is_vague_seek_user_motive(normalized_reason, normalized_action, normalized_motive, normalized_topic):
                score -= 0.07
                note("动机略空,需要更具体的生活钩子")

        action_parts = {part.strip() for part in normalized_action.split("+") if part.strip()}
        if "screen_peek" in action_parts:
            if profile.get("observant"):
                score += 0.07
                note("观察型人格适合轻观察")
            else:
                score -= 0.05
                note("观察能力和人格标记不强")
        if "photo_text" in action_parts:
            if profile.get("visual"):
                score += 0.08
                note("视觉表达贴合人格")
            else:
                score -= 0.04
                note("图片表达缺少人格支撑")
        if "voice" in action_parts:
            if profile.get("voicey"):
                score += 0.08
                note("语音表达贴合人格")
            else:
                score -= 0.04
                note("语音表达缺少人格支撑")
        if "poke" in action_parts:
            if profile.get("playful") or profile.get("clingy"):
                score += 0.06
                note("轻互动贴合俏皮/依恋人格")
            else:
                score -= 0.06
                note("戳一戳不像当前人格的自然动作")

        if normalized_reason in {"activity_share", "diary_share", "background_schedule"}:
            if profile.get("playful") or profile.get("visual") or profile.get("observant"):
                score += 0.04
                note("轻分享和人格气质相容")
        if not normalized_topic and not normalized_motive and normalized_reason in {"check_in", "quiet_care", "state_share"}:
            score -= 0.08
            note("念头缺少具体来源")

        leak_tokens = ("模型", "插件", "action", "模块", "接口", "提示词", "LLM", "prompt", "后台任务", "系统调度")
        if any(token in text for token in leak_tokens):
            score -= 0.28
            blocker = True
            note("内部机制泄露风险")
        worldview_mode = str(
            runtime_persona_setting(self, "worldview_adaptation_mode", "auto") or "auto"
        )
        if worldview_mode in {"fantasy", "sci_fi", "custom"} and any(token in text for token in ("现实网络", "现实设备", "影响现实", "控制设备")):
            score -= 0.25
            blocker = True
            note("世界观边界风险")

        mode = self._current_emotion_gate_mode(user, now=now) or self._current_relationship_gate_mode(user, now=now)
        if mode in {"careful", "hurt", "refusing", "backoff"} and intimate and normalized_source != "timer":
            score -= 0.22
            if mode in {"refusing", "backoff"}:
                blocker = True
            note(f"关系状态 {mode} 不适合亲密主动")

        score = max(0.0, min(1.0, score))
        if not notes:
            note("动机、动作和当前关系基本贴合")
        return {
            "score": score,
            "note": "；".join(notes[:3]),
            "blocker": blocker,
        }

    def _maslow_motivation_profile(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str,
        topic: str = "",
        source: str = "",
        semantic_kind: str = "",
        anchor_type: str = "",
        anchor_score: float = 0.5,
        evidence_text: str = "",
    ) -> dict[str, Any]:
        text = f"{reason} {action} {topic} {motive} {source} {semantic_kind} {anchor_type} {evidence_text}"
        action_parts = {part.strip() for part in str(action or "").split("+") if part.strip()}
        ignored_streak = _safe_int(user.get("ignored_streak"), 0, 0)

        def has_any(tokens: tuple[str, ...]) -> bool:
            return any(token in text for token in tokens)

        layer = "belonging"
        drive = "维持连接"
        score_bias = 0.02
        pressure_bias = 0.0

        if reason == "insomnia_night" or has_any(("困", "睡", "熬夜", "失眠", "休息", "生病", "头疼", "不舒服", "饿", "胃口", "吃点")):
            layer = "physiological"
            drive = "状态照料"
            score_bias = 0.04
            pressure_bias = -0.02
        elif action_parts & {"screen_peek"} or ignored_streak > 0 or has_any(("边界", "别回", "不用回", "忙", "别打扰", "沉默", "未回复")):
            layer = "safety"
            drive = "确认边界"
            score_bias = -0.02 if ignored_streak >= 2 else 0.01
            pressure_bias = 0.04 + min(0.04, ignored_streak * 0.015)
        elif reason == "important_date_share" or has_any(("生日", "纪念", "考试", "面试", "项目", "成绩", "努力", "鼓励", "夸", "辛苦")):
            layer = "esteem"
            drive = "认可支持"
            score_bias = 0.06
            pressure_bias = -0.03
        elif has_any(("意义", "存在", "世界观", "宇宙", "星空", "命运", "现实边界", "精神", "信念")):
            layer = "meaning"
            drive = "意义连接"
            score_bias = 0.03
            pressure_bias = -0.01
        elif reason in {"creative_share", "diary_share", "news_share", "web_exploration_share", "bili_video_share", "activity_share"} or has_any(
            ("学习", "创作", "灵感", "作品", "研究", "新闻", "搜索", "阅读", "视频", "日记", "见闻")
        ):
            layer = "growth"
            drive = "探索成长"
            score_bias = 0.03
            pressure_bias = -0.02 if anchor_score >= 0.5 else 0.02
        elif source in {"pending_followup", "followup"} or semantic_kind == "continuation" or anchor_type == "recent_context":
            layer = "belonging"
            drive = "续接共同话题"
            score_bias = 0.07
            pressure_bias = -0.06
        elif semantic_kind in {"greeting", "light_touch"} or reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            layer = "belonging"
            drive = "轻量陪伴仪式"
            score_bias = 0.03
            pressure_bias = -0.02
        elif reason in {"quiet_care", "check_in"} and anchor_score < 0.45:
            layer = "belonging"
            drive = "无明确由头的关心"
            score_bias = -0.03
            pressure_bias = 0.03

        if action_parts & {"poke", "voice"}:
            pressure_bias += 0.02
        if self._private_user_role(user) == "friend" and layer in {"belonging", "esteem"}:
            score_bias -= 0.02
            pressure_bias += 0.02

        labels = {
            "physiological": "状态",
            "safety": "安全",
            "belonging": "归属",
            "esteem": "尊重",
            "growth": "成长",
            "meaning": "意义",
        }
        return {
            "layer": layer,
            "drive": drive,
            "score_bias": max(-0.12, min(0.12, score_bias)),
            "pressure_bias": max(-0.12, min(0.12, pressure_bias)),
            "note": f"{labels.get(layer, layer)}/{drive}",
        }

    def _proactive_semantic_evidence_text(self, value: Any, *, limit: int = 260) -> str:
        parts: list[str] = []

        def collect(item: Any, depth: int = 0) -> None:
            if len(parts) >= 8 or depth > 2:
                return
            if isinstance(item, dict):
                priority = (
                    "title",
                    "topic",
                    "summary",
                    "text",
                    "content",
                    "reason",
                    "why",
                    "scene",
                    "impulse",
                    "tone",
                    "group_name",
                    "sender_name",
                    "share_decision",
                    "share_tone",
                    "share_boundary",
                )
                for key in priority:
                    if key in item:
                        collect(item.get(key), depth + 1)
                if len(parts) < 4:
                    for key, nested in list(item.items())[:8]:
                        if key not in priority:
                            collect(nested, depth + 1)
            elif isinstance(item, list):
                for nested in item[:5]:
                    collect(nested, depth + 1)
            else:
                text = _single_line(item, 80)
                if text and text not in parts:
                    parts.append(text)

        collect(value)
        return _single_line(" ".join(parts), limit)

    def _proactive_semantic_chain_text(self, chain: list[dict[str, Any]] | None) -> str:
        if not isinstance(chain, list):
            return ""
        parts: list[str] = []
        for step in chain[:4]:
            if not isinstance(step, dict):
                continue
            bits = [
                _single_line(step.get("kind"), 30),
                _single_line(step.get("reason"), 40),
                _single_line(step.get("topic"), 60),
                _single_line(step.get("motive"), 80),
                _single_line(step.get("tone"), 40),
            ]
            line = _single_line(" ".join(bit for bit in bits if bit), 120)
            if line:
                parts.append(line)
        return _single_line(" ".join(parts), 240)

    def _planned_proactive_model_judge_signature(self, user: dict[str, Any]) -> str:
        persona = _single_line(str(self._get_default_persona_prompt() or ""), 800)
        worldview = _single_line(str(self._format_worldview_adaptation_prompt() or ""), 400)
        interaction = user.get("current_interaction") if isinstance(user.get("current_interaction"), dict) else {}
        contact = user.get("contact_preference") if isinstance(user.get("contact_preference"), dict) else {}
        semantics = self._planned_proactive_semantics(user)
        ignored = _safe_int(user.get("ignored_streak"), 0, 0)
        parts = [
            self._private_user_role(user),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40),
            _single_line(user.get("planned_proactive_topic"), 80).casefold(),
            _single_line(user.get("planned_proactive_motive"), 180).casefold(),
            _single_line(semantics.get("kind"), 40),
            _single_line(semantics.get("anchor_type"), 40),
            _single_line(semantics.get("need_layer"), 40),
            _single_line(semantics.get("need_drive"), 80),
            f"semantic={int(_safe_float(semantics.get('score'), 0.5) * 5)}",
            f"pressure={int(_safe_float(semantics.get('pressure'), 0.4) * 5)}",
            f"risk={int(_safe_float(semantics.get('risk'), 0.0) * 5)}",
            interaction.get("expression_band") or "",
            contact.get("mode") or "",
            "ignored=0" if ignored <= 0 else "ignored=1" if ignored == 1 else "ignored=2+",
            _single_line(user.get("last_user_message"), 160).casefold(),
            f"last_user_at={int(_safe_float(user.get('last_user_message_at'), 0))}",
            persona,
            worldview,
        ]
        raw = "\n".join(_single_line(part, 1000) for part in parts)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _cached_proactive_model_judgement(
        self,
        user: dict[str, Any],
        *,
        signature: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not signature:
            return None
        check_now = _engine_host._now_ts() if now is None else now
        ttl = max(
            5,
            _safe_int(
                runtime_persona_setting(self, "proactive_persona_judge_cache_minutes", 180),
                180,
                5,
                720,
            ),
        ) * 60
        cache = user.get("proactive_persona_judge_cache")
        if isinstance(cache, dict):
            entry = cache.get(signature)
            if isinstance(entry, dict):
                judged_at = _safe_float(entry.get("judged_at"), 0)
                cached = entry.get("result")
                if judged_at > 0 and check_now - judged_at <= ttl and isinstance(cached, dict):
                    return dict(cached)
        if _single_line(user.get("planned_proactive_model_judge_signature"), 80) == signature:
            judged_at = _safe_float(user.get("planned_proactive_model_judge_at"), 0)
            cached = user.get("planned_proactive_model_judge_result")
            if judged_at > 0 and check_now - judged_at <= ttl and isinstance(cached, dict):
                return dict(cached)
        return None

    def _proactive_persona_judge_calls_today(self) -> int:
        usage = self.data.get("token_usage") if isinstance(getattr(self, "data", None), dict) else {}
        by_day_task = usage.get("by_day_task") if isinstance(usage, dict) else {}
        today_tasks = by_day_task.get(_today_key()) if isinstance(by_day_task, dict) else {}
        task = today_tasks.get("proactive_persona_judge") if isinstance(today_tasks, dict) else {}
        return _safe_int(task.get("calls"), 0, 0) if isinstance(task, dict) else 0

    def _local_proactive_persona_judgement(self, user: dict[str, Any]) -> dict[str, Any] | None:
        if self._private_user_role(user) == "friend" or _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        semantics = self._planned_proactive_semantics(user)
        alignment = self._planned_proactive_persona_alignment(user)
        if (
            not semantics.get("blocker")
            and not alignment.get("blocker")
            and _safe_float(semantics.get("score"), 0.5) >= 0.78
            and _safe_float(semantics.get("pressure"), 0.4) <= 0.30
            and _safe_float(semantics.get("risk"), 0.0) <= 0.10
            and _safe_float(alignment.get("score"), 0.55) >= 0.78
        ):
            return {"decision": "send", "score": 90, "reason": "本地高置信人格判定", "local": True}
        return None

    def _normalize_proactive_model_judgement(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "defer", "drop"}:
            return None
        score = _safe_int(payload.get("score"), 0, 0, 100)
        threshold_getter = getattr(self, "_effective_proactive_persona_judge_send_threshold", None)
        threshold = (
            threshold_getter()
            if callable(threshold_getter)
            else _safe_int(
                runtime_persona_setting(self, "proactive_persona_judge_send_threshold", 62),
                62,
                0,
                100,
            )
        )
        reason = self._normalize_legacy_proactive_text(payload.get("reason"), limit=140) or "模型人格判定"
        if decision == "send" and score > 0 and score < threshold:
            reason = self._normalize_legacy_proactive_text(
                f"{reason}；分数低于建议阈值，正文生成时收敛",
                limit=140,
            )
        result = {
            "decision": decision,
            "score": score,
            "reason": reason,
            "delay_minutes": _safe_int(payload.get("delay_minutes"), 90, 20, 360),
            "reason_field": self._normalize_legacy_proactive_text(payload.get("planned_reason") or payload.get("reason_field"), limit=40),
            "action": self._normalize_legacy_proactive_text(payload.get("action"), limit=40),
            "topic": _single_line(payload.get("topic"), 80),
            "motive": self._normalize_internal_motive_text(_single_line(payload.get("motive"), 180)),
        }
        if decision == "rewrite" and not any(
            _single_line(result.get(key), 180)
            for key in ("reason_field", "action", "topic", "motive")
        ):
            result["decision"] = "send"
            result["reason"] = self._normalize_legacy_proactive_text(
                f"{reason}；未给出可应用的计划字段，交给正文生成收敛",
                limit=140,
            )
        return result

    def _proactive_model_judgement_requires_hard_block(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
    ) -> bool:
        decision = _single_line(judgement.get("decision"), 20).lower()
        if decision not in {"defer", "drop"}:
            return False
        semantics = self._planned_proactive_semantics(user)
        alignment = self._planned_proactive_persona_alignment(user)
        if (
            bool(semantics.get("blocker"))
            or _safe_float(semantics.get("risk"), 0.0) >= 0.70
            or bool(alignment.get("blocker"))
        ):
            return True
        note = _single_line(judgement.get("reason"), 180)
        hard_markers = (
            "用户明确拒绝",
            "对方明确拒绝",
            "不要再发",
            "不想收到",
            "免打扰",
            "用户明确休息",
            "对方明确休息",
            "用户正在睡",
            "隐私泄露",
            "关系越界",
            "串用户",
            "其他用户专属",
            "内部机制",
            "工具名",
            "插件",
            "提示词",
            "后台任务",
            "系统任务",
            "世界观边界",
            "无真实来源",
            "捏造事实",
            "虚构事实",
            "不安全",
        )
        return any(marker in note for marker in hard_markers)
