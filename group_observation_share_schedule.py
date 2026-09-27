# -*- coding: utf-8 -*-
"""GroupObservationShareScheduleMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import random
import re
from .group_addressing_rules import group_message_addresses_bot
from .group_observation_shared import _persona_value
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from typing import Any



class GroupObservationShareScheduleMixin:
    """GroupObservationShareScheduleMixin（从 GroupObservationMixin 拆出）。"""


    def _group_private_share_candidate(self, group_id: str, group: dict[str, Any], *, trigger_sender_id: str = "") -> dict[str, Any] | None:
        recent = self._filtered_group_recent_messages(group)
        if not recent:
            return None
        now = _now_ts()
        harassment = self._group_bot_harassment_candidate(group_id, group, trigger_sender_id=trigger_sender_id, now=now)
        if isinstance(harassment, dict):
            return harassment
        window = [
            item for item in recent[-60:]
            if isinstance(item, dict) and now - _safe_float(item.get("ts"), 0) <= 75 * 60
        ]
        if not window:
            return None
        texts = [_single_line(item.get("text"), 120) for item in window if _single_line(item.get("text"), 120)]
        joined = "\n".join(texts)
        score = 0
        funny_markers = ("笑死", "哈哈", "草", "绷", "乐", "典", "离谱", "绝了", "蚌埠住", "太好笑", "逆天", "急了")
        share_markers = ("截图", "表情包", "名场面", "好玩", "神了", "破防", "节目效果", "群友", "复读")
        score += sum(2 for marker in funny_markers if marker in joined)
        score += sum(1 for marker in share_markers if marker in joined)
        if len({str(item.get("sender_id") or "") for item in window if item.get("sender_id")}) >= 3:
            score += 1
        if len(window) >= 6:
            score += 1
        if re.search(r"[!?！？]{2,}|哈{2,}|草{2,}", joined):
            score += 2
        active_speakers = len({str(item.get("sender_id") or "") for item in window if item.get("sender_id")})
        topic_threads = group.get("topic_threads") if isinstance(group.get("topic_threads"), list) else []
        active_threads = [
            item for item in topic_threads
            if isinstance(item, dict)
            and now - _safe_float(item.get("last_ts"), 0) <= 75 * 60
            and _safe_int(item.get("message_count"), 0, 0) >= 3
        ]
        best_thread = None
        if active_threads:
            best_thread = max(
                active_threads,
                key=lambda item: (
                    _safe_int(item.get("message_count"), 0, 0)
                    + min(4, len(item.get("participants") if isinstance(item.get("participants"), list) else []))
                    + sum(
                        1
                        for example in (item.get("recent_examples") if isinstance(item.get("recent_examples"), list) else [])
                        if any(marker in str((example or {}).get("text") or "") for marker in funny_markers + share_markers)
                    ),
                    _safe_float(item.get("last_ts"), 0),
                ),
            )
            score += min(5, _safe_int(best_thread.get("message_count"), 0, 0) // 2)
            participants = best_thread.get("participants") if isinstance(best_thread.get("participants"), list) else []
            if len(participants) >= 2:
                score += 2
        if active_speakers >= 4:
            score += 1
        if active_speakers < 2:
            return None
        if score < 6:
            return None
        examples = best_thread.get("recent_examples") if isinstance(best_thread, dict) and isinstance(best_thread.get("recent_examples"), list) else []
        candidate_lines = examples[-6:] if examples else window[-8:]
        chosen = max(
            candidate_lines,
            key=lambda item: (
                sum(1 for marker in funny_markers + share_markers if marker in str(item.get("text") or "")),
                _safe_float(item.get("ts"), 0),
            ),
        )
        speaker_id = str(chosen.get("sender_id") or "")
        speaker = self._group_member_identity_label(speaker_id, chosen.get("identity_name") or chosen.get("name"), limit=24)
        text = _single_line(chosen.get("text"), 100)
        if not text:
            return None
        topic_title = _single_line(best_thread.get("title"), 60) if isinstance(best_thread, dict) else ""
        topic = self._soften_topic_hook(topic_title or text) or "群里那段话题"
        summary_items = []
        for item in candidate_lines[-6:]:
            if not isinstance(item, dict):
                continue
            name = self._group_member_identity_label(
                str(item.get("sender_id") or ""),
                item.get("identity_name") or item.get("name"),
                limit=16,
            )
            line = _single_line(item.get("text"), 56)
            if line:
                summary_items.append(f"{name}: {line}")
        participant_ids = []
        if isinstance(best_thread, dict) and isinstance(best_thread.get("participants"), list):
            participant_ids = [str(item) for item in best_thread.get("participants", []) if str(item)]
        if not participant_ids:
            participant_ids = list(dict.fromkeys(str(item.get("sender_id") or "") for item in window if isinstance(item, dict) and item.get("sender_id")))[:8]
        participant_names = []
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        for participant_id in participant_ids[:8]:
            member = members.get(participant_id) if isinstance(members, dict) else None
            name_hint = member.get("identity_name") or member.get("name") if isinstance(member, dict) else participant_id
            participant_names.append(self._group_member_identity_label(participant_id, name_hint, limit=16))
        latest_ts = max(_safe_float(item.get("ts"), now) for item in window if isinstance(item, dict))
        duration_minutes = max(1, int((latest_ts - min(_safe_float(item.get("ts"), now) for item in window if isinstance(item, dict))) / 60))
        topic_summary = (
            f"这不是单独一句话,而是群里约 {duration_minutes} 分钟里围绕“{topic}”滚起来的一段话题；"
            f"参与者约 {len(participant_names) or active_speakers} 人"
            + (f"（{ '、'.join(participant_names[:5])}）" if participant_names else "")
            + f", 中间最适合转述的点是：{text}"
        )
        return {
            "group_id": str(group_id),
            "kind": "funny",
            "speaker_id": speaker_id,
            "speaker": speaker,
            "topic": topic,
            "text": text,
            "summary": " / ".join(summary_items[-5:]),
            "topic_summary": _single_line(topic_summary, 260),
            "participants": participant_names[:8],
            "window_minutes": duration_minutes,
            "score": score,
            "trigger_sender_id": trigger_sender_id,
            "event_ts": latest_ts,
            "created_ts": now,
            "addressed_to_bot": self._group_observed_message_addresses_bot(chosen),
            "source_talking_to": _single_line(chosen.get("talking_to"), 40),
            "source_talking_to_name": _single_line(chosen.get("talking_to_name"), 80),
            "source_trigger": _single_line(chosen.get("scene_trigger"), 40),
        }

    def _group_observed_message_addresses_bot(self, item: dict[str, Any]) -> bool:
        """Return whether the recorded scene actually points at the Bot."""
        return group_message_addresses_bot(
            item,
            bot_markers=(_persona_value(self, "bot_name", ""), "bot", "机器人", "小星"),
            normalize=_single_line,
        )

    def _group_bot_harassment_candidate(
        self,
        group_id: str,
        group: dict[str, Any],
        *,
        trigger_sender_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any] | None:
        recent = self._filtered_group_recent_messages(group)
        if not recent:
            return None
        now = now or _now_ts()
        window = [
            item for item in recent[-24:]
            if isinstance(item, dict) and now - _safe_float(item.get("ts"), 0) <= 10 * 60
        ]
        if not window:
            return None
        pressure_markers = (
            "出来", "在吗", "人呢", "说话", "别装死", "怎么不回", "快回", "理我",
            "笨蛋", "傻", "蠢", "废物", "垃圾", "闭嘴", "滚", "不会吧", "急了",
        )
        addressed: list[dict[str, Any]] = []
        abusive: list[dict[str, Any]] = []
        by_sender: dict[str, int] = {}
        for item in window:
            text = _single_line(item.get("text"), 140)
            sender_id = str(item.get("sender_id") or "")
            looks_addressed = self._group_observed_message_addresses_bot(item)
            looks_pressuring = any(marker in text for marker in pressure_markers)
            repeated_ping = bool(re.fullmatch(r"[@\s\w\u4e00-\u9fff]{1,12}[?？!！。]*", text)) and looks_addressed
            if looks_addressed:
                addressed.append(item)
                if sender_id:
                    by_sender[sender_id] = by_sender.get(sender_id, 0) + 1
            if looks_addressed and (looks_pressuring or repeated_ping):
                abusive.append(item)
        if not addressed:
            return None
        max_sender_hits = max(by_sender.values(), default=0)
        score = len(addressed) + len(abusive) * 2 + max(0, max_sender_hits - 1)
        if len(addressed) >= 5:
            score += 2
        if max_sender_hits >= 3:
            score += 2
        if score < 6:
            return None
        chosen = abusive[-1] if abusive else addressed[-1]
        latest_ts = max(_safe_float(item.get("ts"), now) for item in window if isinstance(item, dict))
        speaker_id = str(chosen.get("sender_id") or "")
        speaker = self._group_member_identity_label(speaker_id, chosen.get("identity_name") or chosen.get("name"), limit=24)
        text = _single_line(chosen.get("text"), 100)
        summary_items = []
        for item in window[-5:]:
            if not isinstance(item, dict):
                continue
            name = self._group_member_identity_label(
                str(item.get("sender_id") or ""),
                item.get("identity_name") or item.get("name"),
                limit=16,
            )
            line = _single_line(item.get("text"), 56)
            if line:
                summary_items.append(f"{name}: {line}")
        return {
            "group_id": str(group_id),
            "kind": "bot_harassment",
            "speaker_id": speaker_id,
            "speaker": speaker,
            "topic": f"{speaker or '某个成员'} 持续提到 Bot",
            "text": text,
            "summary": " / ".join(summary_items[-4:]),
            "score": score,
            "trigger_sender_id": trigger_sender_id,
            "event_ts": latest_ts,
            "created_ts": now,
            "addressed_to_bot": True,
            "source_talking_to": _single_line(chosen.get("talking_to"), 40) or "bot",
            "source_talking_to_name": _single_line(chosen.get("talking_to_name"), 80) or "你",
            "source_trigger": _single_line(chosen.get("scene_trigger"), 40),
        }

    def _maybe_schedule_group_private_share(self, group_id: str, group: dict[str, Any], *, trigger_sender_id: str = "") -> bool:
        if not _persona_value(self, "enable_group_companion", False):
            return False
        candidate = self._group_private_share_candidate(group_id, group, trigger_sender_id=trigger_sender_id)
        if not isinstance(candidate, dict):
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        now = _now_ts()
        changed = False
        for user_id, user in users.items():
            if not isinstance(user, dict) or not user.get("enabled", True) or not user.get("umo"):
                continue
            if not self._friend_can_receive_proactive_reason(user, "group_share", "message"):
                continue
            target_id = str(user_id)
            if target_id == str(trigger_sender_id or ""):
                continue
            member = members.get(target_id) if isinstance(members, dict) else None
            member_last_seen = _safe_float((member or {}).get("last_seen"), 0) if isinstance(member, dict) else 0
            if member_last_seen <= 0 or now - member_last_seen < 8 * 3600:
                continue
            cooldown_key = f"group_share:{_today_key()}"
            last_key = str(user.get("last_group_share_key") or "")
            last_at = _safe_float(user.get("last_group_share_at"), 0)
            if last_key == cooldown_key or now - last_at < 18 * 3600:
                continue
            timer_event = self._get_active_llm_timer(user)
            if (
                _safe_float(user.get("next_proactive_at"), 0) > 0
                and str(user.get("planned_proactive_source") or "") == "timer"
                and self._llm_timer_can_use_internal_scheduler(timer_event if isinstance(timer_event, dict) else None)
            ):
                continue
            kind = _single_line(candidate.get("kind"), 32) or "funny"
            score = _safe_int(candidate.get("score"), 0, 0)
            chance = (
                min(0.48, 0.20 + score * 0.025)
                if kind == "bot_harassment"
                else min(0.26, 0.06 + score * 0.025)
            )
            if random.random() > chance:
                continue
            delay_minutes = random.randint(18, 45) if kind == "bot_harassment" else random.randint(45, 120)
            scheduled = now + delay_minutes * 60
            topic = _single_line(candidate.get("topic"), 60) or "群里的小片段"
            context = {
                "group_id": str(group_id),
                "group_name": _single_line(group.get("name") or group.get("group_name"), 80),
                "kind": kind,
                "topic": topic,
                "speaker_id": _single_line(candidate.get("speaker_id"), 40),
                "speaker": _single_line(candidate.get("speaker"), 24),
                "text": _single_line(candidate.get("text"), 120),
                "summary": _single_line(candidate.get("summary"), 220),
                "topic_summary": _single_line(candidate.get("topic_summary"), 260),
                "participants": candidate.get("participants") if isinstance(candidate.get("participants"), list) else [],
                "window_minutes": _safe_int(candidate.get("window_minutes"), 0, 0),
                "event_ts": _safe_float(candidate.get("event_ts"), _safe_float(candidate.get("created_ts"), now)),
                "created_ts": now,
                "addressed_to_bot": bool(candidate.get("addressed_to_bot")),
                "source_talking_to": _single_line(candidate.get("source_talking_to"), 40),
                "source_talking_to_name": _single_line(candidate.get("source_talking_to_name"), 80),
                "source_trigger": _single_line(candidate.get("source_trigger"), 40),
            }
            target_absence = self._format_elapsed(now - member_last_seen).removesuffix("前")
            accepted = self._offer_proactive_candidate(
                target_id,
                user,
                {
                    "source": "group_share",
                    "reason": "group_share",
                    "action": "message",
                    "scheduled_ts": scheduled,
                    "topic": topic,
                    "score": score,
                    "motive": (
                        f"群 {group_id} 里有人持续围绕 Bot 互动；{self._group_member_identity_name(target_id, target_id, limit=24)} 已经有 {target_absence}没在群里冒泡，想私下轻轻提一句"
                        if kind == "bot_harassment"
                        else f"群 {group_id} 里有个挺有意思的片段；{self._group_member_identity_name(target_id, target_id, limit=24)} 已经有 {target_absence}没在群里冒泡，想私下轻轻转述一下"
                    ),
                    "context_key": "group_share_context",
                    "context": context,
                },
            )
            if not accepted:
                continue
            user["last_group_share_key"] = cooldown_key
            user["last_group_share_at"] = now
            changed = True
        return changed

    def _maybe_schedule_group_ignore_complaint(
        self,
        group_id: str,
        group: dict[str, Any],
        *,
        sender_id: str = "",
        sender_name: str = "",
        text: str = "",
        now: float | None = None,
    ) -> bool:
        if not sender_id or not _persona_value(self, "enable_group_companion", False):
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        user = users.get(str(sender_id))
        if not isinstance(user, dict) or not user.get("enabled", True) or not user.get("umo"):
            return False
        if self._private_user_role(user, str(sender_id)) == "friend":
            return False
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        last_sent = _safe_float(user.get("last_sent"), 0)
        if awaiting_since <= 0 or last_sent <= 0:
            return False
        now = _now_ts() if now is None else now
        wait_seconds = now - max(awaiting_since, last_sent)
        if wait_seconds < 90 * 60:
            return False
        if _safe_int(user.get("ignored_streak"), 0, 0) <= 0:
            return False
        cooldown_key = f"group_ignore_complaint:{_today_key()}"
        if str(user.get("last_group_ignore_complaint_key") or "") == cooldown_key:
            return False
        if now - _safe_float(user.get("last_group_ignore_complaint_at"), 0) < 24 * 3600:
            return False
        if _safe_float(user.get("next_proactive_at"), 0) > 0 and _safe_float(user.get("next_proactive_at"), 0) <= now + 90 * 60:
            return False
        profile = self._persona_action_profile()
        chance = 0.035
        if profile.get("clingy"):
            chance += 0.055
        if profile.get("playful"):
            chance += 0.035
        if profile.get("observant"):
            chance += 0.015
        if not (profile.get("clingy") or profile.get("playful") or profile.get("observant")):
            chance *= 0.35
        chance += min(0.035, max(0, _safe_int(user.get("ignored_streak"), 0, 0) - 1) * 0.015)
        if random.random() > min(0.16, chance):
            return False
        delay_minutes = random.randint(12, 36)
        display_name = self._group_member_identity_name(str(sender_id), sender_name or str(sender_id), limit=24)
        group_name = _single_line(group.get("name") or group.get("group_name"), 40) or str(group_id)
        accepted = self._offer_proactive_candidate(
            str(sender_id),
            user,
            {
                "source": "group_ignore_complaint",
                "reason": "quiet_care",
                "action": "message",
                "scheduled_ts": now + delay_minutes * 60,
                "topic": "刚才私聊没回但在群里冒泡",
                "score": 68,
                "motive": (
                    f"{display_name} 已经有 {self._format_elapsed(wait_seconds).removesuffix('前')}没回私聊，"
                    f"但刚刚在群 {group_name} 里冒泡了；如果符合人格，可以低压地小声抱怨一句或撒娇一下，不要质问，不要泄露群聊细节。"
                ),
            },
        )
        if not accepted:
            return False
        user["last_group_ignore_complaint_key"] = cooldown_key
        user["last_group_ignore_complaint_at"] = now
        user["last_group_ignore_complaint_group_id"] = str(group_id)
        user["last_group_ignore_complaint_text"] = _single_line(text, 80)
        return True

    def _maybe_schedule_post_goodnight_group_activity(
        self,
        group_id: str,
        group: dict[str, Any],
        *,
        sender_id: str = "",
        sender_name: str = "",
        text: str = "",
        now: float | None = None,
    ) -> bool:
        """Sometimes react when the owner keeps chatting after both sides said goodnight."""
        if not sender_id or not _persona_value(self, "enable_group_companion", False):
            return False
        users = self.data.get("users")
        if not isinstance(users, dict):
            return False
        user = users.get(str(sender_id))
        if not isinstance(user, dict) or not user.get("enabled", True) or not user.get("umo"):
            return False
        if self._private_user_role(user, str(sender_id)) != "owner":
            return False

        now = _now_ts() if now is None else now
        rest_set_at = _safe_float(user.get("user_rest_set_at"), 0)
        rest_kind = _single_line(user.get("user_rest_kind"), 24).lower()
        rest_reason = _single_line(user.get("user_rest_reason"), 120)
        if rest_kind != "sleep" or rest_set_at <= 0 or not re.search(r"晚安|睡|补觉|好梦", rest_reason):
            return False
        if re.search(r"(?:别|不要|先别|暂时别|今晚别|今天别).{0,10}(?:打扰|主动|发消息|找我|回(?:复)?|理我)", rest_reason):
            return False
        if now <= rest_set_at or now - rest_set_at > 4 * 3600:
            return False

        companion_at = _safe_float(user.get("last_companion_message_at"), 0)
        companion_text = _single_line(user.get("last_companion_message"), 180)
        if companion_at < rest_set_at or companion_at > now:
            return False
        if not re.search(r"晚安|睡|休息|好梦|明天", companion_text):
            return False

        episode_key = f"{int(rest_set_at)}:{str(sender_id)}"
        if _single_line(user.get("last_post_goodnight_group_activity_attempt_key"), 80) == episode_key:
            return False
        # One probability draw per goodnight episode, not once per group message.
        user["last_post_goodnight_group_activity_attempt_key"] = episode_key
        user["last_post_goodnight_group_activity_attempt_at"] = now

        profile = self._persona_action_profile()
        chance = 0.10
        if profile.get("playful"):
            chance += 0.12
        if profile.get("clingy"):
            chance += 0.08
        if profile.get("observant"):
            chance += 0.04
        if not (profile.get("playful") or profile.get("clingy") or profile.get("observant")):
            chance *= 0.6
        chance = min(0.34, chance)
        if random.random() > chance:
            return False

        delay_minutes = random.randint(3, 14)
        scheduled = now + delay_minutes * 60
        group_name = _single_line(group.get("name") or group.get("group_name"), 40) or str(group_id)
        display_name = self._group_member_identity_name(str(sender_id), sender_name or str(sender_id), limit=24)
        context = {
            "group_id": str(group_id),
            "group_name": group_name,
            "group_activity_at": now,
            "rest_set_at": rest_set_at,
            "companion_goodnight_at": companion_at,
            "activity_preview": _single_line(text, 80),
            "chance": round(chance, 3),
        }
        accepted = self._offer_proactive_candidate(
            str(sender_id),
            user,
            {
                "source": "post_goodnight_group_activity",
                "reason": "post_goodnight_group_activity",
                "action": "message",
                "scheduled_ts": scheduled,
                "window_start_at": scheduled,
                "preferred_ts": scheduled,
                "best_until_at": scheduled + 16 * 60,
                "expire_at": scheduled + 38 * 60,
                "topic": "互道晚安后又在群里活跃",
                "score": 72,
                "motive": (
                    f"刚和 {display_name} 互道晚安，却又偶然看见对方还在群里活跃。"
                    "结合人格决定要不要轻轻调侃、关心一句，或干脆不点破；"
                    "不要质问、查岗、复述群聊内容或群名，也不要表现成持续监视。"
                ),
                "context_key": "post_goodnight_group_activity_context",
                "context": context,
            },
        )
        if not accepted:
            return False
        user["last_post_goodnight_group_activity_at"] = now
        user["last_post_goodnight_group_activity_group_id"] = str(group_id)
        return True
