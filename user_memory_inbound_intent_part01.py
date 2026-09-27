# -*- coding: utf-8 -*-
"""UserMemoryInboundIntentPart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_inbound_intent.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 462 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryInboundIntentMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .user_memory_render_shared import logger
from datetime import datetime
from typing import Any



class UserMemoryInboundIntentPart01Mixin:
    """UserMemoryInboundIntentPart01Mixin（从 UserMemoryInboundIntentMixin 拆出）。"""


    def _time_bucket_for_user_habit(self, when: datetime | None = None) -> tuple[str, int]:
        when = when or datetime.now()
        minute = when.hour * 60 + when.minute
        buckets = (
            ("凌晨", 0, 6 * 60),
            ("早晨", 6 * 60, 9 * 60),
            ("上午", 9 * 60, 11 * 60 + 30),
            ("中午", 11 * 60 + 30, 14 * 60),
            ("下午", 14 * 60, 18 * 60),
            ("傍晚", 18 * 60, 20 * 60),
            ("夜晚", 20 * 60, 23 * 60),
            ("深夜", 23 * 60, 24 * 60),
        )
        for label, start, end in buckets:
            if start <= minute < end:
                return label, minute
        return "凌晨", minute

    def _classify_user_habit_message(self, text: str) -> tuple[str, str, str]:
        cleaned = _single_line(text, 220)
        lowered = cleaned.lower()
        if not cleaned:
            return "", "", ""
        compact = re.sub(r"\s+", "", cleaned)
        if self._user_habit_message_is_noise(cleaned):
            return "", "", ""
        category = ""
        topic = ""
        profile = self._detect_private_user_retrieval_habit(cleaned)
        if profile:
            category = "固定检索"
            topic = _single_line(profile.get("topic"), 80) or cleaned
        elif re.fullmatch(r"(?:早|早安|早上好|午安|中午好|晚上好|晚安)(?:呀|啊|哦|喔|啦|～|~|！|!)?", compact):
            category = "互动习惯"
            topic = "日常问候"
        elif re.fullmatch(r"(?:摸摸|抱抱|贴贴|亲亲){1,4}(?:呀|啊|哦|啦|～|~|！|!)?", compact):
            category = "互动习惯"
            topic = "亲昵互动"
        elif re.fullmatch(r"(?:你)?(?:在干嘛|在做什么|做什么呢|在吗)(?:呀|啊|呢|？|\?)?", compact):
            category = "互动习惯"
            topic = "询问近况"
        elif any(token in cleaned for token in ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯")):
            category = "偏好习惯"
            topic = cleaned
        elif self._user_habit_has_self_state(cleaned, "饮食"):
            category = "饮食节奏"
            if any(token in cleaned for token in ("还没", "没吃", "没来得及", "没饭", "没到饭点")):
                topic = "还没吃/饭点偏晚"
            elif any(token in cleaned for token in ("吃了", "刚吃", "吃完", "饱")):
                topic = "已经吃过饭"
            else:
                topic = "吃饭相关"
        elif self._user_habit_has_self_state(cleaned, "作息"):
            category = "作息节奏"
            if any(token in cleaned for token in ("还没睡", "睡不着", "熬夜")):
                topic = "夜里还没睡"
            elif any(token in cleaned for token in ("起床", "刚醒", "醒了")):
                topic = "起床/刚醒"
            else:
                topic = "睡眠相关"
        elif self._user_habit_has_self_state(cleaned, "学习工作"):
            category = "学习工作"
            topic = "学习/工作节奏"
        elif self._user_habit_has_self_state(cleaned, "娱乐"):
            category = "娱乐习惯"
            topic = "娱乐/刷内容"
        if not category or not topic:
            return "", "", ""
        signature_core = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9_]+", "", topic.casefold())[:80]
        signature = f"{category}|{signature_core}"
        return category, _single_line(topic, 80), signature

    @staticmethod
    def _user_habit_message_is_noise(text: str) -> bool:
        cleaned = _single_line(text, 240)
        lowered = cleaned.lower()
        if not cleaned or len(cleaned) > 180:
            return True
        if any(token in lowered for token in (
            "bili_live_probe", "bili状态", "photo_share", "private_companion", "<timer", "<tts",
            "我转发了一段聊天记录", "你看看里面在说什么", "合并转发", "聊天记录",
        )):
            return True
        if re.search(r"^(?:私聊|群聊)?(?:告诉|转告|提醒|叫|转发给).{1,40}", cleaned):
            return True
        if re.search(r"(?:帮我|能去|可以去).{0,12}(?:告诉|叫|转告|提醒).{1,40}", cleaned):
            return True
        return False

    @staticmethod
    def _user_habit_has_self_state(text: str, kind: str) -> bool:
        cleaned = _single_line(text, 180)
        if not cleaned or re.search(r"(?:你|他|她|它|别人|群里).{0,8}", cleaned[:20]):
            return False
        markers = {
            "饮食": r"(?:我.{0,10}(?:吃|饿|饱)|(?:还没吃|没吃|刚吃|吃完|饿了|好饿|饱了|去吃饭|准备吃))",
            "作息": r"(?:我.{0,10}(?:睡|醒|困|起床|熬夜)|(?:睡觉啦|准备睡|去睡了|刚睡醒|醒了|起床了|困了|睡不着|还没睡))",
            "学习工作": r"(?:我.{0,12}(?:学习|上班|下班|工作|上课|下课|写作业|考试|摸鱼)|(?:去上班|下班了|上课了|下课了|写作业|准备考试))",
            "娱乐": r"(?:我.{0,12}(?:玩|看|刷|追)|(?:在玩|去玩|在看|刚看|最近看|正在刷|准备看).{0,20}(?:游戏|视频|番|漫画|小说|直播)?)",
        }
        return bool(re.search(markers.get(kind, r"$^"), cleaned))

    def _detect_private_user_retrieval_habit(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 220)
        compact = re.sub(r"\s+", "", cleaned).lower()
        if not compact:
            return {}
        is_question = bool(re.search(r"[？?]|什么|啥|哪|几|多少|有没有|吗|呢|了没|了吗|颜色|色", compact))
        if not is_question:
            return {}
        if any(token in compact for token in ("衣服", "穿搭", "穿着", "穿什么", "穿了什么", "裙子", "外套", "上衣", "校服", "裤子", "鞋子")) and any(
            token in compact for token in ("颜色", "什么色", "啥色", "什么颜色", "穿什么", "穿了什么", "今天穿", "现在穿")
        ):
            return {
                "intent": "current_outfit_query",
                "topic": "询问 Bot 当前穿着/衣服颜色",
                "query_anchors": ["当前穿搭", "今日穿搭", "每日穿搭", "衣服颜色", "穿什么", "穿了什么", "daily_outfit", "persona_life"],
                "answer_hints": ["优先检索今日穿搭图、当前日程和最近自我生活记忆", "回答时直接说当前准确穿着和颜色,不要泛泛说可能"],
            }
        if any(token in compact for token in ("吃了什么", "吃什么", "晚饭", "午饭", "早餐", "夜宵")) and any(token in compact for token in ("你", "bot", "星缘", "今天", "刚才", "现在")):
            return {
                "intent": "current_meal_query",
                "topic": "询问 Bot 最近吃了什么",
                "query_anchors": ["self_meal", "吃了什么", "午餐", "晚餐", "早餐", "夜宵", "persona_life"],
                "answer_hints": ["优先检索 Bot 自我进食记录和当前日程", "如果没有准确记录,说明没记清,不要编具体食物"],
            }
        if any(token in compact for token in ("在干嘛", "在做什么", "忙什么", "现在做", "刚才做")) and any(token in compact for token in ("你", "bot", "星缘", "现在", "刚才", "今天")):
            return {
                "intent": "current_activity_query",
                "topic": "询问 Bot 当前/最近在做什么",
                "query_anchors": ["当前日程", "日程细化", "self_timeline", "persona_life", "在做什么"],
                "answer_hints": ["优先检索当前日程、日程细化和自我时间线", "按最近准确记录回答,不要把很久前的状态当现在"],
            }
        return {}

    def _update_user_behavior_habits_from_message(self, user: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return
        cleaned = _single_line(text, 220)
        if not cleaned or cleaned.startswith(("/", "!", "！", "#")):
            return
        sleep_delay_detector = getattr(self, "_detect_sleep_delay_request", None)
        if callable(sleep_delay_detector):
            try:
                if sleep_delay_detector(cleaned):
                    return
            except Exception:
                pass
        category, topic, signature = self._classify_user_habit_message(cleaned)
        if not category or not signature:
            return
        now_dt = datetime.now()
        day_key = now_dt.strftime("%Y-%m-%d")
        bucket, minute = self._time_bucket_for_user_habit(now_dt)
        habits = user.setdefault("behavior_habits", {})
        if not isinstance(habits, dict):
            habits = {}
            user["behavior_habits"] = habits
        patterns = habits.setdefault("patterns", [])
        if not isinstance(patterns, list):
            patterns = []
            habits["patterns"] = patterns
        self._sanitize_user_behavior_habit_patterns(user)
        patterns = habits.get("patterns") if isinstance(habits.get("patterns"), list) else []
        key = f"{bucket}|{category}|{signature}"
        matched = None
        for item in patterns:
            if isinstance(item, dict) and str(item.get("key") or "") == key:
                matched = item
                break
        if matched is None:
            matched = {
                "key": key,
                "bucket": bucket,
                "category": category,
                "topic": topic,
                "signature": signature,
                "count": 0,
                "avg_minute": minute,
                "examples": [],
                "created_ts": _now_ts(),
            }
            patterns.append(matched)
        retrieval_profile = self._detect_private_user_retrieval_habit(cleaned)
        if retrieval_profile:
            matched["intent"] = _single_line(retrieval_profile.get("intent"), 60)
            matched["query_anchors"] = [
                _single_line(item, 40)
                for item in retrieval_profile.get("query_anchors", [])
                if _single_line(item, 40)
            ][:12]
            matched["answer_hints"] = [
                _single_line(item, 80)
                for item in retrieval_profile.get("answer_hints", [])
                if _single_line(item, 80)
            ][:8]
            matched["memory_key"] = hashlib.sha1(
                f"{str(user.get('user_id') or user.get('id') or '')}|{key}".encode("utf-8", errors="ignore")
            ).hexdigest()[:20]
        count = _safe_int(matched.get("count"), 0, 0) + 1
        old_avg = _safe_float(matched.get("avg_minute"), minute)
        matched["count"] = min(999, count)
        matched["avg_minute"] = round((old_avg * max(0, count - 1) + minute) / max(1, count), 1)
        matched["last_seen_ts"] = _now_ts()
        matched["last_seen_text"] = cleaned
        evidence_days = matched.get("evidence_days")
        if not isinstance(evidence_days, list):
            evidence_days = []
        evidence_days.append(day_key)
        matched["evidence_days"] = list(dict.fromkeys(str(item) for item in evidence_days if str(item)))[-30:]
        examples = matched.get("examples")
        if not isinstance(examples, list):
            examples = []
        examples.insert(0, cleaned)
        matched["examples"] = list(dict.fromkeys(_single_line(item, 90) for item in examples if _single_line(item, 90)))[:5]
        patterns.sort(
            key=lambda item: (
                _safe_int(item.get("count"), 0, 0) if isinstance(item, dict) else 0,
                _safe_float(item.get("last_seen_ts"), 0) if isinstance(item, dict) else 0,
            ),
            reverse=True,
        )
        del patterns[runtime_persona_setting(self, "user_habit_max_items", 24):]
        habits["updated_at"] = now_dt.strftime("%Y-%m-%d %H:%M")
        self._maybe_sync_user_behavior_habit_to_memory_companion(user, matched)

    def _sanitize_user_behavior_habit_patterns(self, user: dict[str, Any]) -> bool:
        habits = user.get("behavior_habits") if isinstance(user, dict) else None
        if not isinstance(habits, dict):
            return False
        patterns = habits.get("patterns")
        if not isinstance(patterns, list):
            return False
        allowed_categories = {
            "固定检索", "互动习惯", "偏好习惯", "饮食节奏", "作息节奏", "学习工作", "娱乐习惯",
        }
        kept: list[dict[str, Any]] = []
        for item in patterns:
            if not isinstance(item, dict) or str(item.get("category") or "") not in allowed_categories:
                continue
            evidence_days = item.get("evidence_days")
            if not isinstance(evidence_days, list) or not any(str(day) for day in evidence_days):
                continue
            kept.append(item)
        if len(kept) == len(patterns):
            return False
        habits["patterns"] = kept[: runtime_persona_setting(self, "user_habit_max_items", 24)]
        habits["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        return True

    def _maybe_sync_user_behavior_habit_to_memory_companion(self, user: dict[str, Any], habit: dict[str, Any]) -> None:
        if not isinstance(user, dict) or not isinstance(habit, dict):
            return
        if str(habit.get("category") or "") != "固定检索":
            return
        min_count = max(2, runtime_persona_setting(self, "user_habit_min_count", 3))
        if _safe_int(habit.get("count"), 0, 0) < min_count:
            return
        now = _now_ts()
        if now - _safe_float(habit.get("memory_synced_at"), 0) < 12 * 3600:
            return
        recorder = getattr(self, "_memory_companion_record_user_habit", None)
        if not callable(recorder):
            return
        user_id = _single_line(user.get("user_id") or user.get("id"), 80)
        if not user_id:
            return
        habit["memory_synced_at"] = now
        operation = recorder(user=user, user_id=user_id, habit=dict(habit))
        try:
            creator = getattr(self, "_create_lifecycle_background_task", None)
            task = (
                creator(operation, label="user_habit_memory_sync")
                if callable(creator)
                else asyncio.create_task(operation, name="private-companion-user-habit-memory-sync")
            )
            if task is None:
                raise RuntimeError("background task unavailable")
            if not callable(creator):
                def consume(done_task: asyncio.Task) -> None:
                    try:
                        done_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(
                            "用户习惯记忆同步后台任务失败: %s",
                            _single_line(exc, 160),
                        )

                task.add_done_callback(consume)
        except Exception:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            habit["memory_synced_at"] = 0

    def _format_user_habit_time(self, minute_value: Any) -> str:
        minute = int(max(0, min(1439, round(_safe_float(minute_value, 0)))))
        return f"{minute // 60:02d}:{minute % 60:02d}"

    @staticmethod
    def _minute_distance(a: float, b: float) -> float:
        diff = abs(float(a) - float(b)) % 1440
        return min(diff, 1440 - diff)

    def _user_habit_effective_score(self, item: dict[str, Any], *, now: float | None = None) -> float:
        now = now or _now_ts()
        evidence_days = item.get("evidence_days")
        count = (
            len(set(str(day) for day in evidence_days if str(day)))
            if isinstance(evidence_days, list)
            else 0
        )
        age_days = max(0.0, (now - _safe_float(item.get("last_seen_ts"), now)) / 86400)
        if age_days <= 7:
            recency = 1.0
        elif age_days <= 30:
            recency = max(0.2, 1.0 - (age_days - 7) / 23 * 0.8)
        else:
            recency = 0.0
        return count * recency

    def _qualified_user_behavior_habits(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        self._sanitize_user_behavior_habit_patterns(user)
        habits = user.get("behavior_habits")
        if not isinstance(habits, dict):
            return []
        patterns = habits.get("patterns")
        if not isinstance(patterns, list):
            return []
        now = _now_ts()
        min_count = max(2, runtime_persona_setting(self, "user_habit_min_count", 3))
        kept = []
        for item in patterns:
            if not isinstance(item, dict):
                continue
            if now - _safe_float(item.get("last_seen_ts"), now) > 30 * 86400:
                continue
            if _safe_int(item.get("count"), 0, 0) < min_count:
                continue
            evidence_days = item.get("evidence_days")
            if not isinstance(evidence_days, list) or len(set(str(day) for day in evidence_days if str(day))) < min_count:
                continue
            if self._user_habit_effective_score(item, now=now) < max(1.6, min_count * 0.45):
                continue
            kept.append(item)
        kept.sort(
            key=lambda item: (
                self._user_habit_effective_score(item, now=now),
                _safe_float(item.get("last_seen_ts"), 0),
            ),
            reverse=True,
        )
        return kept

    def _user_habit_related_to_text(self, item: dict[str, Any], text: str) -> bool:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return False
        category = str(item.get("category") or "")
        topic = _single_line(item.get("topic"), 80)
        mapping = {
            "饮食节奏": ("吃", "饭", "早餐", "午饭", "晚饭", "夜宵", "饿", "饱", "零食", "喝"),
            "作息节奏": ("睡", "醒", "起床", "熬夜", "困", "晚安", "早安", "梦"),
            "学习工作": ("作业", "上课", "下课", "考试", "题", "学习", "上班", "下班", "工作", "摸鱼"),
            "娱乐习惯": ("游戏", "视频", "番", "漫画", "小说", "直播", "刷", "看"),
            "固定提问": ("？", "?", "什么", "多少", "吗", "呢", "怎么", "有没有", "要不要"),
            "偏好习惯": ("喜欢", "讨厌", "想要", "以后", "每天", "经常", "总是", "习惯"),
        }
        if any(token in cleaned for token in mapping.get(category, ())):
            return True
        tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", topic)
        return any(token and token in cleaned for token in tokens)

    def _natural_user_habit_line(self, item: dict[str, Any]) -> str:
        bucket = _single_line(item.get("bucket"), 12)
        category = _single_line(item.get("category"), 20)
        topic = _single_line(item.get("topic"), 80)
        if not topic:
            return ""
        if category == "饮食节奏":
            if "还没吃" in topic or "饭点偏晚" in topic:
                return f"{bucket}时对方常会提到还没吃饭，聊到吃的可以轻轻接住，不用像提醒。"
            if "已经吃过" in topic:
                return f"{bucket}时对方常已经吃过饭，别每次都追问吃没吃。"
            return f"{bucket}时对方容易聊到吃饭，相关时顺手接住就好。"
        if category == "作息节奏":
            if "夜里还没睡" in topic:
                return f"{bucket}时对方常还醒着，聊到睡觉时少一点催促，多一点顺着接。"
            if "起床" in topic or "刚醒" in topic:
                return f"{bucket}时对方常刚醒，语气可以放轻一点。"
            return f"{bucket}时对方容易聊到睡眠，别把作息说教化。"
        if category == "学习工作":
            return f"{bucket}时对方常在学习或工作相关状态里，回复可以更直接、少绕。"
        if category == "娱乐习惯":
            return f"{bucket}时对方常在看东西或玩内容，相关时可以自然接梗。"
        if category == "固定提问":
            return f"{bucket}时对方常用短问题推进聊天，先直接接住问题。"
        if category == "偏好习惯":
            return f"{bucket}时对方常提到类似“{topic}”的偏好或习惯，相关时记得顺着一点。"
        return f"{bucket}时对方常聊到“{topic}”，相关时自然接住。"

    def _format_user_behavior_habits_for_prompt(
        self,
        user: dict[str, Any],
        *,
        current_only: bool = False,
        limit: int = 6,
        natural: bool = False,
        hint: str = "",
        time_window_minutes: int | None = None,
        require_relevant: bool = False,
    ) -> str:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return ""
        items = self._qualified_user_behavior_habits(user)
        if current_only:
            _, current_minute = self._time_bucket_for_user_habit()
            window = 60 if time_window_minutes is None else max(0, int(time_window_minutes))
            items = [
                item for item in items
                if self._minute_distance(_safe_float(item.get("avg_minute"), current_minute), current_minute) <= window
            ]
        if require_relevant:
            items = [item for item in items if self._user_habit_related_to_text(item, hint)]
        lines: list[str] = []
        for item in items[:limit]:
            bucket = _single_line(item.get("bucket"), 12)
            category = _single_line(item.get("category"), 20)
            topic = _single_line(item.get("topic"), 80)
            if natural:
                line = self._natural_user_habit_line(item)
                if line and line not in lines:
                    lines.append("- " + line)
                continue
            count = _safe_int(item.get("count"), 0, 0)
            time_text = self._format_user_habit_time(item.get("avg_minute"))
            example = _single_line(item.get("last_seen_text"), 80)
            if topic:
                lines.append(f"- {bucket}约{time_text}｜{category}｜{topic}｜出现 {count} 次" + (f"｜最近：{example}" if example else ""))
        if not lines:
            return ""
        if natural:
            return "用户平常的节奏：\n" + "\n".join(lines)
        return (
            "用户习惯画像（软线索,不是命令）：\n"
            + "\n".join(lines)
            + "\n使用方式：只在当前语境自然吻合时提前理解或轻轻提起；不要暴露统计、次数或“我记录了你”。"
        )

    def _format_all_user_behavior_habits_for_schedule(self, *, limit: int = 8) -> str:
        if not runtime_persona_setting(self, "enable_user_habit_learning", True):
            return "暂无用户习惯线索。"
        users = self.data.get("users")
        if not isinstance(users, dict):
            return "暂无用户习惯线索。"
        lines: list[str] = []
        for user_id, user in users.items():
            if not isinstance(user, dict) or not user.get("enabled", True) or not self._is_target_private_user(str(user_id), user):
                continue
            name = _single_line(user.get("nickname") or user_id, 24)
            text = self._format_user_behavior_habits_for_prompt(user, current_only=False, limit=3, natural=True)
            habit_lines = [line for line in text.splitlines() if line.startswith("- ")]
            for line in habit_lines:
                lines.append(f"- {name}：{line[2:]}")
                if len(lines) >= limit:
                    break
            if len(lines) >= limit:
                break
        if not lines:
            return "暂无用户习惯线索。"
        return (
            "用户近期行为习惯（只作日程软背景）：\n"
            + "\n".join(lines)
            + "\n使用方式：只帮助判断对方常出现的时段和话题,不要把用户习惯、食物偏好或避雷直接改写成 Bot 今天必须执行的购买、带饭、约饭或准备任务。"
        )
