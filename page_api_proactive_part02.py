# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiProactivePart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_proactive.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 313 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiProactiveMixin）。
"""
from __future__ import annotations
from .page_api_proactive_shared import Any
from .page_api_proactive_shared import _REASON_TEXT



class PrivateCompanionPageApiProactivePart02Mixin:
    """PrivateCompanionPageApiProactivePart02Mixin（从 PrivateCompanionPageApiProactiveMixin 拆出）。"""


    def _proactive_chat_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        installed = False
        detector = getattr(self.plugin, "_integrated_plugin_installed", None)
        if callable(detector):
            try:
                installed = bool(detector("astrbot_plugin_proactive_chat"))
            except Exception:
                installed = False
        enabled = bool(getattr(self.plugin, "enable_proactive_chat_integration", True))
        review_mode = str(getattr(self.plugin, "proactive_chat_bridge_review_mode", "local") or "local")
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        linked_users = 0
        last_sent_at = 0.0
        for user in users.values():
            if not isinstance(user, dict):
                continue
            sent_at = self._float(user.get("proactive_chat_bridge_last_sent_at"))
            if sent_at <= 0:
                continue
            linked_users += 1
            last_sent_at = max(last_sent_at, sent_at)
        formatter = getattr(self.plugin, "_format_timestamp_elapsed", None)
        last_sent = formatter(last_sent_at) if last_sent_at > 0 and callable(formatter) else ""
        runtime_status: dict[str, Any] = {}
        runtime_bridge = getattr(self.plugin, "_proactive_chat_runtime_bridge", None)
        status_getter = getattr(runtime_bridge, "status", None)
        if callable(status_getter):
            try:
                value = status_getter()
                runtime_status = value if isinstance(value, dict) else {}
            except Exception as exc:
                runtime_status = {
                    "mode": "fallback",
                    "mode_label": "发送前兼容",
                    "last_error": self._single_line(exc, 160),
                }
        runtime_mode = str(runtime_status.get("mode") or ("fallback" if installed and enabled else "waiting"))
        runtime_label = str(runtime_status.get("mode_label") or ("发送前兼容" if installed and enabled else "等待运行实例"))
        return {
            "installed": installed,
            "enabled": enabled,
            "active": bool(installed and enabled),
            "deep_active": bool(runtime_status.get("attached")),
            "runtime_degraded": bool(runtime_status.get("degraded")),
            "runtime_mode": runtime_mode,
            "runtime_mode_label": runtime_label,
            "runtime_version": self._single_line(runtime_status.get("version"), 40),
            "runtime_method_count": self._int(runtime_status.get("method_count")),
            "runtime_methods": list(runtime_status.get("methods") or []),
            "runtime_last_event": self._single_line(runtime_status.get("last_event"), 180),
            "runtime_last_error": self._single_line(runtime_status.get("last_error"), 180),
            "runtime_missing_methods": list(runtime_status.get("missing_methods") or []),
            "runtime_open_attempts": self._int(runtime_status.get("open_attempt_count")),
            "runtime_counters": dict(runtime_status.get("counters") or {}),
            "scope": "private",
            "review_mode": review_mode,
            "review_mode_label": "跟随主动终审" if review_mode == "follow_proactive_review" else "轻量本地复核",
            "linked_user_count": linked_users,
            "last_sent_at": last_sent_at,
            "last_sent": last_sent,
        }

    def _proactive_only_mode_snapshot(self) -> dict[str, Any]:
        clearer = getattr(self.plugin, "_clear_proactive_only_temp_unlocks_if_mode_off", None)
        if callable(clearer):
            clearer()
        unlocks_getter = getattr(self.plugin, "_proactive_only_unlock_store", None)
        label_getter = getattr(self.plugin, "_proactive_only_unlock_label", None)
        related_getter = getattr(self.plugin, "_related_proactive_only_unlock_keys", None)
        unlocks = sorted(unlocks_getter() if callable(unlocks_getter) else [])

        def label(key: str) -> str:
            return label_getter(key) if callable(label_getter) else key

        related: dict[str, list[dict[str, str]]] = {}
        locked_keys = [
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_passive_topic_suppression",
            "enable_environment_perception",
            "enable_message_debounce",
            "enable_recall_enhancement",
            "enable_private_image_self_recognition",
            "enable_forward_message_adaptation",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_food_menu_recommendation",
            "enable_reading_archive_preference_influence",
            "enable_worldbook_member_recognition",
            "enable_atrelay_tools",
            "enable_livingmemory_integration",
            "enable_tts_enhancement",
            "enable_segmented_proactive_reply",
        ]
        for key in locked_keys:
            keys = related_getter(key) if callable(related_getter) else []
            related[key] = [{"key": item, "label": label(item)} for item in keys]
        return {
            "enabled": bool(getattr(self.plugin, "enable_proactive_only_mode", False)),
            "unlocked": [{"key": key, "label": label(key)} for key in unlocks],
            "related": related,
        }

    def _proactive_intensity_summary(self) -> dict[str, Any]:
        runtime_getter = getattr(self.plugin, "_proactive_intensity_runtime", None)
        runtime = runtime_getter() if callable(runtime_getter) else {}
        if not isinstance(runtime, dict):
            runtime = {}
        effects = runtime.get("effects") if isinstance(runtime.get("effects"), dict) else {}

        def call_or_attr(method_name: str, attr_name: str, default: Any) -> Any:
            method = getattr(self.plugin, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    pass
            return getattr(self.plugin, attr_name, default)

        effective_max_daily = call_or_attr("_runtime_max_daily_messages", "max_daily_messages", 0)
        effective_group_interject_max_daily = call_or_attr(
            "_effective_group_interject_max_daily",
            "group_interject_max_daily",
            2,
        )
        limit_is_unlimited = getattr(self.plugin, "_proactive_daily_limit_is_unlimited", None)
        limit_formatter = getattr(self.plugin, "_format_proactive_daily_limit", None)
        max_daily_unlimited = bool(limit_is_unlimited(effective_max_daily)) if callable(limit_is_unlimited) else False
        group_interject_unlimited = bool(limit_is_unlimited(effective_group_interject_max_daily)) if callable(limit_is_unlimited) else False
        effective = {
            "max_daily_messages": effective_max_daily,
            "max_daily_messages_text": limit_formatter(effective_max_daily) if callable(limit_formatter) else str(effective_max_daily),
            "max_daily_messages_unlimited": max_daily_unlimited,
            "idle_minutes": effects.get("idle_minutes", getattr(self.plugin, "idle_minutes", 0)),
            "min_interval_minutes": effects.get("min_interval_minutes", getattr(self.plugin, "min_interval_minutes", 0)),
            "proactive_persona_judge_send_threshold": call_or_attr(
                "_effective_proactive_persona_judge_send_threshold",
                "proactive_persona_judge_send_threshold",
                62,
            ),
            "proactive_review_strength": call_or_attr("_effective_proactive_review_strength", "proactive_review_strength", "lenient"),
            "group_wakeup_cooldown_seconds": call_or_attr(
                "_effective_group_wakeup_cooldown_seconds",
                "group_wakeup_cooldown_seconds",
                90,
            ),
            "group_high_intensity_cooldown_seconds": call_or_attr(
                "_effective_group_high_intensity_cooldown_seconds",
                "group_high_intensity_cooldown_seconds",
                150,
            ),
            "group_interject_min_interval_minutes": call_or_attr(
                "_effective_group_interject_min_interval_minutes",
                "group_interject_min_interval_minutes",
                180,
            ),
            "group_interject_max_daily": effective_group_interject_max_daily,
            "group_interject_max_daily_text": limit_formatter(effective_group_interject_max_daily) if callable(limit_formatter) else str(effective_group_interject_max_daily),
            "group_interject_max_daily_unlimited": group_interject_unlimited,
            "group_wakeup_interest_probability": call_or_attr(
                "_effective_group_wakeup_interest_probability",
                "group_wakeup_interest_probability",
                0.18,
            ),
            "group_wakeup_question_threshold": call_or_attr(
                "_effective_group_wakeup_question_threshold",
                "group_wakeup_question_threshold",
                65,
            ),
            "group_wakeup_cold_group_threshold": call_or_attr(
                "_effective_group_wakeup_cold_group_threshold",
                "group_wakeup_cold_group_threshold",
                65,
            ),
            "ignore_token_soft_limit": bool(effects.get("ignore_token_soft_limit", False)),
            "ignore_daily_limit": bool(effects.get("ignore_daily_limit", False)),
        }
        configured = {
            "max_daily_messages": getattr(self.plugin, "max_daily_messages", 0),
            "max_daily_messages_text": str(getattr(self.plugin, "max_daily_messages", 0)),
            "max_daily_messages_unlimited": False,
            "idle_minutes": getattr(self.plugin, "idle_minutes", 0),
            "min_interval_minutes": getattr(self.plugin, "min_interval_minutes", 0),
            "proactive_persona_judge_send_threshold": getattr(self.plugin, "proactive_persona_judge_send_threshold", 62),
            "proactive_review_strength": getattr(self.plugin, "proactive_review_strength", "lenient"),
            "group_wakeup_cooldown_seconds": getattr(self.plugin, "group_wakeup_cooldown_seconds", 90),
            "group_high_intensity_cooldown_seconds": getattr(self.plugin, "group_high_intensity_cooldown_seconds", 150),
            "group_interject_min_interval_minutes": getattr(self.plugin, "group_interject_min_interval_minutes", 180),
            "group_interject_max_daily": getattr(self.plugin, "group_interject_max_daily", 2),
            "group_interject_max_daily_text": str(getattr(self.plugin, "group_interject_max_daily", 2)),
            "group_interject_max_daily_unlimited": False,
            "group_wakeup_interest_probability": getattr(self.plugin, "group_wakeup_interest_probability", 0.18),
            "group_wakeup_question_threshold": getattr(self.plugin, "group_wakeup_question_threshold", 65),
            "group_wakeup_cold_group_threshold": getattr(self.plugin, "group_wakeup_cold_group_threshold", 65),
            "ignore_token_soft_limit": False,
            "ignore_daily_limit": False,
        }
        changed = [
            key
            for key, value in effective.items()
            if str(value) != str(configured.get(key))
        ]
        return {
            "preset": self._single_line(runtime.get("preset") or "off", 40),
            "enabled": bool(runtime.get("enabled")),
            "label": self._single_line(runtime.get("label") or "关闭预设", 40),
            "description": self._single_line(runtime.get("description"), 160),
            "configured": configured,
            "effective": effective,
            "changed_keys": changed,
            "note": "预设只覆盖运行态有效频率，不改写手动参数；最高档使用每日 25 条私聊配额并忽略 Token 软限额降载，但免打扰、休息、用户拒绝、隐私和每日 Token 硬限额仍然生效。",
        }

    def _proactive_template_text(self, value: Any, *, target_name: Any = "", limit: int = 220) -> str:
        text = self._single_line(value, limit)
        if not text:
            return ""
        name = self._single_line(target_name, 40) or "对方"
        return self._single_line(text.replace("{name}", name).replace("{{name}}", name), limit)

    def _proactive_reason_label(self, reason: Any, *, target_name: Any = "") -> str:
        key = self._single_line(reason, 40)
        if not key:
            return "未记录原因"
        extra = {
            "bookshelf_reading_share": "跟你提起刚翻到的漫画资料",
            "bookshelf_recommendation_request": "想问你要不要推荐阅读",
            "web_exploration_share": "分享主动搜索后的发现",
            "news_share": "分享刚读到的新闻",
            "environment_change": "注意到外面的环境突然变了",
            "weather_alert": "收到一条与当前位置有关的气象预警",
            "personal_goal_progress": "自己的一个长期目标有了新进展",
            "timer": "聊天中形成的临时约定",
            "troubleshooting_test": "排障测试触发",
        }
        return self._proactive_template_text(extra.get(key) or _REASON_TEXT.get(key) or key, target_name=target_name, limit=80)

    def _proactive_source_meta(self, source: Any) -> dict[str, str]:
        key = self._single_line(source, 40)
        if not key:
            return {"label": "插件主动", "note": ""}
        meta = {
            "random": {
                "label": "轻微想念",
                "note": "没有明确外部触发，更像安静一阵后轻轻冒出来、想靠近你一下。",
            },
            "daily_greeting": {
                "label": "日常招呼",
                "note": "到了早中晚合适的那个点，顺手来冒个头，不是专门执行问候任务。",
            },
            "pending_followup": {
                "label": "补一句",
                "note": "前面那句还留着一个具体点没落地，所以隔一阵再接一句。",
            },
            "state": {
                "label": "身体小需求",
                "note": "不是汇报状态，而是身体上那点小事挂着，顺手拿来找你说一句。",
            },
            "event": {"label": "生活事件", "note": ""},
            "story": {"label": "日常剧情", "note": ""},
            "habit": {"label": "习惯关心", "note": ""},
            "bilibili": {"label": "B站分享", "note": ""},
            "bookshelf_reading": {"label": "资料归档", "note": ""},
            "creative_writing": {"label": "创作灵感", "note": ""},
            "group_share": {"label": "群聊见闻", "note": ""},
            "web_exploration": {"label": "主动搜索", "note": ""},
            "news": {"label": "新闻阅读", "note": ""},
            "environment_change": {"label": "环境突变", "note": "实时环境出现明显变化后形成的短时主动。"},
            "weather_alert": {"label": "气象预警", "note": "官方预警出现、更新或解除后形成的主要用户提醒。"},
            "body_monitor": {"label": "身体状态联动", "note": "由 Body Monitor 提供的短时身体状态关心事件。"},
            "meal_care": {"label": "饭点关心", "note": "在合适饭点形成的低压力饮食关心。"},
            "group_ignore_complaint": {
                "label": "群内冒泡关心",
                "note": "对方暂未回复私聊、但刚在群内出现后形成的低压力关心。",
            },
            "post_goodnight_group_activity": {
                "label": "晚安后群聊活跃",
                "note": "和主要用户互道晚安后，对方仍在群里活跃时按人格低概率形成的轻调侃或关心。",
            },
            "reading_archive": {"label": "资料归档", "note": ""},
            "personal_goal": {"label": "个人目标", "note": "非创作型长期目标在真实推进、停滞或完成后形成的主动。"},
            "candidate": {"label": "主动候选", "note": ""},
            "followup": {"label": "补一句", "note": "前面的话还差个具体点，所以顺手再接一句。"},
            "external": {"label": "外部主动能力", "note": ""},
            "timer": {"label": "官方定时计划", "note": ""},
            "proactive": {"label": "插件主动", "note": ""},
            "unknown": {"label": "未记录来源", "note": ""},
        }
        return dict(meta.get(key) or {"label": key, "note": ""})

    def _proactive_source_label(self, source: Any) -> str:
        return self._proactive_source_meta(source).get("label") or "插件主动"

    def _proactive_source_note(self, source: Any) -> str:
        return self._single_line(self._proactive_source_meta(source).get("note"), 120)

    def _proactive_reason_detail(
        self,
        *,
        reason: Any,
        source: Any = "",
        topic: Any = "",
        motive: Any = "",
        note: Any = "",
        target_name: Any = "",
    ) -> str:
        label = self._proactive_reason_label(reason, target_name=target_name)
        parts = [label]
        topic_text = self._proactive_template_text(topic, target_name=target_name, limit=80)
        motive_text = self._proactive_template_text(motive, target_name=target_name, limit=140)
        note_text = self._proactive_template_text(note, target_name=target_name, limit=120)
        source_text = self._proactive_source_label(source)
        if topic_text:
            parts.append(f"话题：{topic_text}")
        if motive_text:
            parts.append(f"动机：{motive_text}")
        if note_text:
            parts.append(f"记录：{note_text}")
        if source_text:
            parts.append(f"来源：{source_text}")
        return self._single_line("；".join(parts), 220)
