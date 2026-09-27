# -*- coding: utf-8 -*-
"""动机域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 303 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

import random
import re
from .helpers import _safe_int, _single_line
from typing import Any



class ProactiveEngineMotiveMixin:
    """动机域（从 ProactiveEngineMixin 拆出）。"""


    def _motive_action_bias(self, motive: str) -> dict[str, float]:
        text = str(motive or "")
        return {
            "screen_peek": 0.32 if any(token in text for token in ("还在忙", "埋进去", "看你", "确认", "忙太久", "偷看一眼")) else 0.0,
            "photo_text": 0.34 if any(token in text for token in ("顺手拍", "拍给你", "发你看", "光", "雨", "窗边", "晚霞", "小猫", "桌上", "一幕", "书页", "食堂", "饮料", "便利店", "影子", "倒影", "杯", "包装", "车窗", "门口")) else 0.0,
            "poke": 0.24 if any(token in text for token in ("戳", "碰你一下", "冒头", "轻轻叫你一下", "刷存在感")) else 0.0,
            "voice": 0.3 if any(token in text for token in ("懒得打字", "留句语音", "小声说", "睡不着", "不想敲字")) else 0.0,
        }

    def _choose_proactive_motive(
        self,
        reason: str,
        user: dict[str, Any],
        *,
        action: str = "message",
        planned_event: dict[str, Any] | None = None,
    ) -> str:
        state = self.data.get("daily_state", {})
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        weather_topic_available = self._ordinary_weather_topic_available(user)
        current_item = self._proactive_current_agenda_item()
        snapshot = self._current_story_plan_snapshot()
        last_user_message = _single_line(user.get("last_user_message"), 48)
        can_do = self.data.get("can_do", [])
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)

        topic = ""
        scene = ""
        tone = ""
        impulse = ""
        event_hint = _single_line(snapshot.get("event"), 60) if isinstance(snapshot, dict) else ""
        summary_hint = _single_line(snapshot.get("summary"), 60) if isinstance(snapshot, dict) else ""
        if isinstance(planned_event, dict):
            topic = self._soften_topic_hook(planned_event.get("topic"))
            scene = _single_line(planned_event.get("scene"), 60)
            tone = _single_line(planned_event.get("tone"), 24)
            impulse = _single_line(planned_event.get("impulse"), 60)
        if not topic and isinstance(snapshot, dict):
            topic = self._soften_topic_hook(snapshot.get("topic") or snapshot.get("event"))
        if not scene and isinstance(snapshot, dict):
            scene = _single_line(snapshot.get("scene"), 60)
        if not tone and isinstance(snapshot, dict):
            tone = _single_line(snapshot.get("tone"), 24)
        if not impulse and isinstance(snapshot, dict):
            impulse = _single_line(snapshot.get("impulse"), 60)
        if not topic and current_item:
            topic = _single_line(current_item.get("title"), 36)
        if not topic and isinstance(can_do, list) and can_do and reason == "activity_share":
            topic = _single_line(_engine_host.random.choice(can_do), 28)
        if not topic:
            topic = self._choose_proactive_topic(reason, user)
        location_scene_getter = getattr(self, "_mobile_user_proactive_scene", None)
        try:
            location_scene = location_scene_getter(user) if callable(location_scene_getter) else {}
        except Exception:
            location_scene = {}
        place_kind = _single_line(location_scene.get("place_kind"), 24)
        if location_scene.get("recent_arrival") and reason in {
            "check_in", "quiet_care", "state_share", "morning_greeting", "noon_greeting", "evening_greeting",
        }:
            if place_kind == "home":
                return self._normalize_internal_motive_text("刚到家，想顺手跟你说一声")
            if place_kind == "work":
                return self._normalize_internal_motive_text("到公司后缓下来一点，想顺手跟你说一声")
        if (
            location_scene.get("matched")
            and place_kind == "work"
            and reason in {"environment_change", "weather_alert"}
            and any(token in weather for token in ("雨", "阵雨", "雷雨", "暴雨"))
        ):
            return self._normalize_internal_motive_text("现在人在公司，雨天提醒更适合落在下班回家前")
        if self._private_user_role(user) == "friend":
            if reason in {"quiet_care", "check_in", "state_share"}:
                return _engine_host.random.choice([
                    "作为朋友想到对方可能正忙，只问一句，不要求立刻回复",
                    "朋友之间顺手关心一下近况,说完就把空间留给对方",
                    "看到前面的话题还有一点余味,礼貌地补一句就停",
                ])
            if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
                return _engine_host.random.choice([
                    "按次要用户关系顺手打个招呼,语气轻一点,不显得黏人",
                    "这个时间点刚好想起对方",
                ])
            if reason in {"activity_share", "diary_share", "background_schedule"}:
                if topic:
                    return self._normalize_internal_motive_text(f"有个和“{topic}”有关的小片段")
                return "有个小片段想分享"
            if reason == "group_share":
                return "共同群里有个和对方可能有关的小片段"
        if impulse:
            return self._normalize_internal_motive_text(impulse)
        if scene or tone or event_hint or summary_hint:
            mood_fragment = ""
            if tone in {"安静", "柔和", "松弛", "轻快", "迷糊", "慵懒"}:
                mood_fragment = f",整个人有点{tone}"
            lived_line = ""
            if topic and event_hint:
                lived_line = f"刚刚{event_hint}之后，还想着“{topic}”{mood_fragment}"
            elif scene and topic:
                lived_line = f"在{scene}的时候，想到“{topic}”{mood_fragment}"
            elif event_hint:
                lived_line = f"刚刚{event_hint}的时候{mood_fragment}"
            elif scene:
                lived_line = f"刚刚在{scene}的时候{mood_fragment}"
            elif summary_hint:
                lived_line = f"这一小段安静下来时{mood_fragment}"
            if lived_line:
                return self._normalize_internal_motive_text(lived_line)

        if reason == "birthday_eve_hint":
            return "明天想让对方放松一点"
        if reason == "birthday_celebration":
            return "今天是对方生日，想留一份小惊喜"
        if reason == "birthday_makeup":
            return "昨天错过了祝福，今天补上"
        if reason == "birthday_afterglow":
            return "昨天的开心还没散"
        if reason == "birthday_curiosity":
            return "好奇对方的生日"

        if reason == "special_day_greeting":
            context = user.get("planned_special_day_context") if isinstance(user.get("planned_special_day_context"), dict) else {}
            title = _single_line(context.get("observance_title"), 32) or "这个特别的日子"
            return f"今天是{title}，想在这个时间点先和对方说一句"

        if reason == "insomnia_night":
            motives = [
                "夜里一直没睡着",
                "睡不着，想看看对方是不是也还醒着",
                "已经很晚了，但还是想说一句",
            ]
            if action == "voice":
                motives.append("夜里不想打太多字，想发语音")
            return _engine_host.random.choice(motives)
        if reason == "state_share":
            motives = [
                "这会儿说话可能慢一点",
                "这会儿不太想说太多",
                "这一会儿比较安静，想慢慢说一句",
            ]
            if energy < 45:
                motives.append("不太想说长句，但想看看那边还在不在")
            return _engine_host.random.choice(motives)
        if reason == "quiet_care":
            motives = [
                "刚刚有点在意用户是不是又忙太久了",
                "想起用户最近的状态，想问一句现在怎么样",
                "本来不想打扰，但还是想看看那边还好不好",
            ]
            if last_user_message:
                motives.append(f"想起用户前面提过“{last_user_message}”，有点放心不下")
            elif topic:
                motives.append(f"刚刚想到“{topic}”的时候，也想起用户那边")
            return _engine_host.random.choice(motives)
        if reason == "group_share":
            share = user.get("group_share_context") if isinstance(user.get("group_share_context"), dict) else {}
            group_id = _single_line(share.get("group_id"), 24)
            speaker = _single_line(share.get("speaker"), 24) or "群友"
            text = _single_line(share.get("text"), 70)
            if _single_line(share.get("kind"), 32) == "bot_harassment":
                if text:
                    return self._normalize_internal_motive_text(
                        f"共同群 {group_id} 里有人持续提到 Bot,{speaker} 那句“{text}”还挺扎眼,但只想很轻地跟你提一下"
                    )
                return self._normalize_internal_motive_text(f"共同群 {group_id} 里有人持续提到 Bot,只想很轻地跟你提一下")
            if text:
                return self._normalize_internal_motive_text(
                    f"共同群 {group_id} 里有个小转折,{speaker} 那句“{text}”还留着点余味,想顺手给你递一下"
                )
            return self._normalize_internal_motive_text("共同群里有个小片段还有点余味,想顺手给你递一下")
        if reason == "activity_share":
            motives = [
                "刚刚碰到一个小片段",
                "看到一个小东西",
                "有个小想法",
                "脑子里冒出一句没头没尾的话",
                "一个小想法放着没用",
                "手边的小东西有点好笑，想给你看",
            ]
            if topic:
                motives.append(f"刚碰到“{topic}”时")
            if weather_topic_available and not (place_kind == "work" and location_scene.get("matched")) and any(token in weather for token in ("雨", "小雨", "阵雨")):
                motives.append("外面在下雨")
            if weather_topic_available and any(token in weather for token in ("晴", "阳光", "晚霞")):
                motives.append("外面光线不错")
            return _engine_host.random.choice(motives)
        if reason == "diary_share":
            return _engine_host.random.choice([
                "翻到今天记下来的小片段",
                "看到今天写下来的那句话，觉得可以给你看看",
                "今天有个小片段还记着",
                "有句话不算重要，但一直记着，想给你看看",
                "今天有个小片段还记着",
            ])
        if reason == "memory_echo":
            echo = user.get("memory_echo_context") if isinstance(user.get("memory_echo_context"), dict) else {}
            residue_type = _single_line(echo.get("residue_type"), 24) or "聊天余韵"
            return f"昨天聊过的{residue_type}今天又自然浮上来，想轻轻接一句"
        if reason == "mood_checkin":
            return "还惦记昨天那点不舒服，想轻轻问一句今天有没有好一点"
        if reason == "absence_miss":
            return "隔了几天没聊，有一点想念，但不想让对方有必须回应的压力"
        if reason == "game_invite":
            game = user.get("game_invite_context") if isinstance(user.get("game_invite_context"), dict) else {}
            return f"想起上次的{_single_line(game.get('game_label'), 36) or '那局游戏'}，有点想再约一局"
        if reason == "important_date_share":
            return _engine_host.random.choice([
                "怕用户转头又忘，就先提醒一句",
                "今天这个时间点该提醒一下用户",
                "还记着这件事，所以想提醒用户一句",
            ])
        if reason == "background_schedule":
            motives = [
                "手上的事告一段落了",
                "忙到能休息一小会儿了",
                "眼前这一小段缓下来了",
            ]
            if topic:
                motives.append(f"手上这点“{topic}”还没结束")
            return _engine_host.random.choice(motives)
        if reason == "morning_greeting":
            return _engine_host.random.choice([
                "还没太清醒，先打个招呼",
                "刚醒，先打个招呼",
            ])
        if reason == "noon_greeting":
            return _engine_host.random.choice([
                "中午有点懒",
                "午间松下来了",
            ])
        if reason == "evening_greeting":
            return _engine_host.random.choice([
                "晚上安静下来了",
                "白天快结束了",
            ])
        motives = [
            "刚好休息一下",
            "还记着眼前这点小事",
            "刚松一口气",
        ]
        return self._normalize_internal_motive_text(_engine_host.random.choice(motives))

    def _normalize_internal_motive_text(self, text: str) -> str:
        cleaned = _single_line(text, 80)
        if not cleaned:
            return ""
        replacements = {
            "顺手冒了个头": "",
            "冒个头": "",
            "冒个泡": "",
            "刷一下存在感": "",
            "没什么大道理,就是": "",
            "没什么大不了的,就是": "",
            "顺手晃到你这边了": "",
            "顺手晃到你这边": "",
            "一直不理我": "那边还安静着",
            "不理我": "那边还安静着",
            "怎么一点动静都没有": "那边还没什么动静",
            "怎么还没动静": "那边还没什么动静",
            "一点动静都没有": "那边还没什么动静",
            "主要用户": "这边",
            "次要用户": "对方",
            "用户": "你",
        }
        for src, dst in replacements.items():
            cleaned = cleaned.replace(src, dst)
        cleaned = re.sub(r"(?:主动)?(?:小)?念头", "小想法", cleaned)
        cleaned = re.sub(r"这个念头前面忍过一次[,，]?但它还没散[,，]?", "", cleaned)
        cleaned = re.sub(r"刚才差点想说[,，]?后来又先收住了[,，]?", "", cleaned)
        cleaned = re.sub(r"这会儿又绕回[“\"]([^”\"]{1,40})[”\"]", r"想到“\1”", cleaned)
        cleaned = cleaned.replace("忍过一次", "先放了放")
        cleaned = cleaned.replace("还没散", "还记着")
        cleaned = re.sub(r"(?:来找你一下){2,}", "来找你一下", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(",。 ")
        return cleaned

    def _is_vague_seek_user_motive(self, reason: str, action: str, motive: str, topic: str = "") -> bool:
        if str(action or "message") != "message":
            return False
        if str(reason or "") not in {
            "check_in",
            "quiet_care",
            "state_share",
            "morning_greeting",
            "noon_greeting",
            "evening_greeting",
        }:
            return False
        text = f"{_single_line(motive, 140)} {_single_line(topic, 80)}"
        if not text.strip():
            return True
        concrete_tokens = (
            "前面提过", "刚刚想到“", "天气", "雨", "阳光", "晚霞", "日记", "群", "照片",
            "新闻", "日期", "生日", "纪念", "考试", "作业", "吃饭", "睡", "生病", "压力",
            "低压关心", "收敛情绪",
        )
        if any(token in text for token in concrete_tokens):
            return False
        vague_tokens = (
            "想跟你说一句", "想确认你还在", "确认用户在不在", "确认一下用户状态",
            "想看你在不在", "来看看你", "想来看看你", "来找你", "想找你",
            "只是想", "就是想", "没什么事", "没什么动机", "普通问候",
            "想到你了", "先想到你", "晃到了你", "拐到了你", "碰一下你",
            "轻轻问你一句", "先冒出来的是你", "就想顺手跟你说句话",
        )
        return any(token in text for token in vague_tokens)
