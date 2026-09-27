# -*- coding: utf-8 -*-
"""话题选择域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 205 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

import random
import re
from .helpers import _safe_float, _single_line
from typing import Any



class ProactiveEngineTopicMixin:
    """话题选择域（从 ProactiveEngineMixin 拆出）。"""


    def _format_content_choice_options_for_prompt(self, action: Any = None) -> str:
        terms = self._worldview_terms()
        if terms.get("mode") == "fantasy":
            object_examples = "营火边、行囊、靴扣、地图角、药草包、酒馆杯沿、委托纸、斗篷边、旅店窗、书页边缘"
            record_examples = "旅记、委托备忘、魔法笔记、读到的藏书里的一小句,或某个没写完的标题"
            photo_examples = "适合用水晶映像或随手画面递给熟人的具体场景"
        elif terms.get("mode") == "sci_fi":
            object_examples = "终端边、舱窗、随身包、杯沿、数据板、照明条、维修工具、航行日志、制服边角、资料页边缘"
            record_examples = "航行日志、终端备忘、读到的资料/影像流里的一小句,或某个没写完的标题"
            photo_examples = "适合用终端快照递给熟人的具体画面"
        else:
            object_examples = "桌边、手边、路上、食物、衣物、门口、杯沿、包装、车窗、书页边缘"
            record_examples = "日记、备忘录、作业、阅读/刷到内容里的一小句,或某个没写完的标题"
            photo_examples = "任何当前场景里适合顺手拍给熟人的具体画面"
        has_action_limit = action is not None and bool(str(action).strip())
        normalized_action = str(action or "").strip().lower()
        is_photo_action = "photo" in normalized_action or "image" in normalized_action or normalized_action in {"selfie", "text2img"}
        is_touch_action = "poke" in normalized_action
        is_voice_action = "voice" in normalized_action or "tts" in normalized_action
        options: list[str] = []
        if not has_action_limit:
            options.extend(
                [
                    f"- 眼前物：从当前{terms['schedule']}里的{object_examples}等具体物件里自选一个。",
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    f"- 记录碎片：{record_examples}。",
                    f"- 可拍画面：{photo_examples},不限定天气。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        elif is_photo_action:
            options.extend(
                [
                    f"- 眼前物：从当前{terms['schedule']}里的{object_examples}等具体物件里自选一个。",
                    f"- 可拍画面：{photo_examples},不限定天气。",
                ]
            )
        elif is_touch_action:
            options.extend(
                [
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        elif is_voice_action:
            options.extend(
                [
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    f"- 记录碎片：{record_examples}。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        else:
            options.extend(
                [
                    f"- 眼前物：从当前{terms['schedule']}里的{object_examples}等具体物件里自选一个。",
                    "- 脑内念头：一句突然冒出来的短想法、吐槽、联想或没头没尾的小结论。",
                    "- 输入残留：上一轮聊天留下的余味、没接完的话、想补但没正式补的一点。",
                    f"- 记录碎片：{record_examples}。",
                    "- 关系试探：想靠近但不直说的半句、说完就停，不追问。",
                ]
            )
        if has_action_limit and not is_photo_action:
            options.append("- 可拍画面：本轮不是发图动作时不能选；不要在正文里声称拍照、发图或递照片。")
        return (
            "给模型的内容选择菜单,只供内部单选,不要把类别名写进正文：\n"
            + "\n".join(options)
            + "\n单选规则：先选且只选一个正文锚点；正文只围绕这个锚点展开,不要把两个以上动机、画面、旧话题或关系试探并列拼接。"
            "人格、当前时间段、日程和聊天历史只能用于筛选锚点和调整语气,不能各自贡献一段内容。"
            "如果动机、话题、日程、聊天历史指向不同内容,优先保留最贴近本次动作和当前日程的一项,其余全部舍弃。"
            "避免复用示例词。不要反复使用草稿纸、小画、画圆圈、笔尖划来划去这类廉价重复桥段。"
        )

    def _busy_proactive_voice_context(self) -> dict[str, Any]:
        """Return the current busy schedule context without making it a hard gate."""
        getter = getattr(self, "_busy_reply_context", None)
        if not callable(getter):
            return {"busy": False, "reason": "unavailable", "schedule": "", "until": 0.0}
        try:
            context = getter()
        except Exception:
            return {"busy": False, "reason": "error", "schedule": "", "until": 0.0}
        if not isinstance(context, dict):
            return {"busy": False, "reason": "invalid", "schedule": "", "until": 0.0}
        return {
            "busy": bool(context.get("busy")),
            "reason": _single_line(context.get("reason"), 40),
            "schedule": _single_line(context.get("schedule"), 220),
            "until": _safe_float(context.get("until"), 0.0),
        }

    @staticmethod
    def _busy_voice_reason_eligible(reason: str) -> bool:
        """Keep hands-free voice for social snippets, not operational notices."""
        return reason in {
            "check_in",
            "quiet_care",
            "state_share",
            "background_schedule",
            "activity_share",
            "diary_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
            "insomnia_night",
            "important_date_share",
        }

    def _soften_topic_hook(self, text: str) -> str:
        cleaned = _single_line(text, 60)
        if not cleaned:
            return ""
        cleaned = re.sub(r"[""\"'《》<>]", "", cleaned).strip("，,。！？；： ")
        cleaned = re.sub(r"^(?:关于|有关|一种|一些|那个|这段|这一段)", "", cleaned).strip()
        return cleaned

    def _choose_proactive_topic(self, reason: str, user: dict[str, Any]) -> str:
        if reason == "birthday_eve_hint":
            return "明天给自己留一点空白"
        if reason == "birthday_celebration":
            return "今天只属于你的生日小惊喜"
        if reason == "birthday_makeup":
            return "迟到一点的生日祝福"
        if reason == "birthday_afterglow":
            return "昨天留下的一点开心"
        if reason == "birthday_curiosity":
            return "你的生日是哪一天"
        if reason == "group_share":
            share = user.get("group_share_context") if isinstance(user.get("group_share_context"), dict) else {}
            return _single_line(share.get("topic"), 48) or _single_line(share.get("text"), 48) or "群里那段片段"
        if reason == "bili_video_share":
            video = user.get("bilibili_video_context") if isinstance(user.get("bilibili_video_context"), dict) else {}
            return _single_line(video.get("title"), 48) or "B站视频"
        if reason == "news_share":
            news = user.get("news_context") if isinstance(user.get("news_context"), dict) else {}
            return _single_line(news.get("topic") or news.get("headline"), 48) or "一条新闻"
        if reason == "web_exploration_share":
            exploration = user.get("web_exploration_context") if isinstance(user.get("web_exploration_context"), dict) else {}
            return _single_line(exploration.get("topic") or exploration.get("query"), 48) or "新发现"
        if reason == "creative_share":
            creative = user.get("creative_share_context") if isinstance(user.get("creative_share_context"), dict) else {}
            return _single_line(creative.get("title"), 48) or "刚写到的小说片段"
        if reason == "memory_echo":
            echo = user.get("memory_echo_context") if isinstance(user.get("memory_echo_context"), dict) else {}
            return _single_line(echo.get("residue"), 48) or _single_line(echo.get("summary"), 48) or "昨天聊天留下的一点余韵"
        if reason == "mood_checkin":
            mood = user.get("mood_checkin_context") if isinstance(user.get("mood_checkin_context"), dict) else {}
            return _single_line(mood.get("residue"), 48) or "昨天那点不舒服"
        if reason == "absence_miss":
            return "隔了几天没聊留下的一点想念"
        if reason == "game_invite":
            game = user.get("game_invite_context") if isinstance(user.get("game_invite_context"), dict) else {}
            return f"再玩一局{_single_line(game.get('game_label'), 36) or '上次那款游戏'}"
        current_item = self._proactive_current_agenda_item()
        snapshot = self._current_story_plan_snapshot()
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        weather_topic_available = self._ordinary_weather_topic_available(user)
        location_scene_getter = getattr(self, "_mobile_user_proactive_scene", None)
        try:
            location_scene = location_scene_getter(user) if callable(location_scene_getter) else {}
        except Exception:
            location_scene = {}
        last_user_message = _single_line(user.get("last_user_message"), 24)
        if location_scene.get("recent_arrival") and reason in {
            "check_in", "quiet_care", "state_share", "morning_greeting", "noon_greeting", "evening_greeting",
        }:
            if _single_line(location_scene.get("place_kind"), 24) == "home":
                return "刚到家后的这一小段"
            if _single_line(location_scene.get("place_kind"), 24) == "work":
                return "到公司后的这会儿"
        if (
            location_scene.get("matched")
            and _single_line(location_scene.get("place_kind"), 24) == "work"
            and reason in {"environment_change", "weather_alert"}
            and any(token in weather for token in ("雨", "阵雨", "雷雨", "暴雨"))
        ):
            return "下班前的这场雨"
        snapshot_topic = self._soften_topic_hook(snapshot.get("topic")) if isinstance(snapshot, dict) else ""
        if snapshot_topic:
            return snapshot_topic
        snapshot_event = self._soften_topic_hook(snapshot.get("event")) if isinstance(snapshot, dict) else ""
        if snapshot_event:
            return snapshot_event
        if isinstance(current_item, dict):
            activity = _single_line(current_item.get("activity"), 30)
            if activity:
                activity = re.sub(r"[,、]?\s*想起了[^,。]+", "", activity).strip(",。 ")
                activity = re.sub(r"[,、]?\s*突然想到[^,。]+", "", activity).strip(",。 ")
                if activity:
                    return self._soften_topic_hook(activity)
        if reason in {"activity_share", "diary_share"}:
            if _engine_host.random.random() < 0.88 or not weather_topic_available:
                return self._pick_life_thought_topic(reason)
            if any(token in weather for token in ("雨", "小雨", "阵雨")):
                return "外面那阵雨声"
            if any(token in weather for token in ("晴", "阳光", "晚霞", "多云")):
                return "刚刚那点天色"
        if reason == "morning_greeting":
            return "刚醒那会儿"
        if reason == "noon_greeting":
            return "中午这会儿有点懒"
        if reason == "evening_greeting":
            return "晚一点的这会儿"
        if reason == "quiet_care" and last_user_message:
            return last_user_message
        return ""
