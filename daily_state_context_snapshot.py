# -*- coding: utf-8 -*-
"""上下文快照域。

由 tools/split_mixin_domain.py 从 daily_state.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / {10991, 10992, 10993, 10994, 10995, 10996, 10997, 10998, 10999, 11000, 11001, 11002, 11003, 11004, 11005, 11006, 11007, 11008, 11009, 11010, 11011, 11012, 11013, 11014, 11015, 11016, 11017, 11018, 11019, 11020, 11021, 11022, 11023, 11024, 11025, 11026, 11027, 11028, 11029, 11030, 11032, 11033, 10642, 10643, 10644, 10645, 10646, 10647, 10648, 10728, 10729, 10730, 10731, 10732, 10733, 10734, 10735, 10736, 10737, 10738, 10739, 10740, 10741, 10742, 10743, 10744, 10745, 10746, 10747, 10748, 10750, 10751, 10752, 10753, 10754, 10755, 10756, 10757, 10758, 10759, 10760, 10761, 10762, 10763, 10764, 10766, 10767, 10768, 10769, 10770, 10771, 10772, 10773, 10774, 10775, 10776, 10777, 10778, 10779, 10780, 10781, 10782, 10784, 10785, 10786, 10787, 10788, 10789, 10790, 10791, 10792, 10793, 10794, 10795, 10796, 10797, 10798, 10800, 10801, 10802, 10803, 10804, 10805, 10806, 10807, 10808, 10809, 10810, 10811, 10812, 10813, 10814, 10815, 10816, 10818, 10819, 10820, 10821, 10822, 10823, 10824, 10825, 10826, 10827, 10828, 10829, 10830, 10831, 10832, 10833, 10834, 10835, 10837, 10838, 10839, 10840, 10841, 10842, 10843, 10844, 10845, 10846, 10847, 10848, 10849, 10850, 10851, 10852, 10853, 10854, 10855, 10857, 6762, 6763, 6764, 6765, 6766, 6767, 6768, 6769, 10858, 6771, 6772, 6773, 6774, 6775, 6776, 6777, 6778, 6779, 6780, 6781, 6782, 6783, 6784, 6785, 6786, 6787, 6788, 6789, 6790, 6791, 6792, 6793, 6794, 6795, 6796, 6797, 6798, 6799, 6800, 6801, 6802, 6803, 6804, 6805, 6806, 6807, 6808, 6809, 6810, 6811, 6812, 10900, 6814, 6815, 6816, 6817, 6818, 6819, 6820, 6821, 6822, 6823, 6824, 6825, 6826, 6827, 6828, 10916, 6830, 6831, 6832, 10920, 6834, 6835, 6836, 6837, 6838, 6839, 6840, 6841, 6842, 6843, 6844, 6845, 6846, 6847, 6848, 6849, 6850, 6851, 6852, 6853, 6854, 6855, 6856, 6857, 6858, 6859, 10948, 6861, 6862, 6863, 6864, 6865, 6866, 6867, 6868, 6869, 6870, 6871, 6872, 6873, 6874, 6875, 6876, 6877, 6878, 6879, 6880, 6881, 6882, 10970, 6884, 6885, 6886, 6887, 6888, 6889, 6890, 6891, 6892, 6893, 6894, 6895, 6896, 6897, 6898, 6899, 6900, 6901, 6902, 6903, 6904, 6905, 6906, 6907, 6908, 6909, 6910, 6911, 6912, 6913, 6914, 6915, 6916, 6917, 6918, 6919, 6920, 6921, 6922, 6923, 6924, 6925, 6926, 6927, 6928, 6929, 6930, 6931, 6932, 6933, 6934, 6935, 6936, 6937, 6938, 6939, 6940, 6941, 6942, 6943, 6944, 6945, 6946, 11034, 11035, 11036, 11037, 11038, 11039, 11040, 11041, 11042, 11043, 11044, 11045, 11046, 11047, 11048, 11049, 11050, 11051, 11052, 11053, 11054, 11055, 11056, 11057, 11058, 11059, 11060, 11061, 11062, 11063, 11064, 11065, 11066, 11067, 11068, 11069, 11070, 11071, 11072, 11073, 11074, 11075, 11076, 11077, 11078, 11079, 11080, 11081, 11082, 11083, 11084, 11085, 11086, 11087, 11088, 11089, 11090, 11091, 11092, 11093, 11094, 11095, 11096, 11097, 11098, 11099, 11100, 11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109, 11110, 11111, 11112, 11113, 11114, 11115, 11116, 11117, 11118, 11119, 11120, 11121, 11122, 11123, 11124, 11125, 11126, 11127, 11128, 11129, 11130, 11131, 11132, 11133, 11134, 11135, 11136, 11137, 11138, 11139, 11140, 11141, 11142, 11143, 11144, 11145, 11146, 11147, 10859, 10860, 10861, 10862, 10863, 10864, 10865, 10866, 10867, 10868, 10869, 10870, 10871, 10872, 10873, 10874, 10875, 10876, 10877, 10878, 10879, 10880, 10881, 10882, 10884, 10885, 10886, 10887, 10888, 10889, 10890, 10891, 10892, 10893, 10894, 10895, 10896, 10897, 10898, 10899, 10901, 10902, 10903, 10904, 10905, 10906, 10907, 10908, 10909, 10910, 10911, 10912, 10913, 10914, 10915, 10917, 10918, 10919, 10921, 10922, 10923, 10924, 10925, 10926, 10927, 10928, 10930, 10931, 10932, 10933, 10934, 10935, 10936, 10937, 10938, 10939, 10940, 10941, 10942, 10943, 10944, 10945, 10946, 10949, 10950, 10951, 10952, 10953, 10954, 10955, 10956, 10957, 10958, 10959, 10960, 10961, 10962, 10963, 10964, 10965, 10966, 10967, 10968, 10969, 10971, 10972, 10973, 10974, 10975, 10976, 10977, 10978, 10979, 10980, 10981, 10982, 10984, 10985, 10986, 10987, 10988, 10989, 10990} 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMixin）。
"""
from __future__ import annotations

import random
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import (
    _normalize_photo_subject_owner,
    _now_ts,
    _photo_subject_owner_prompt_label,
    _safe_float,
    _safe_int,
    _single_line,
)
from .memo_notes import memo_note_due_state, memo_note_sort_key, normalize_memo_note
from .persona_config import runtime_persona_setting
from copy import deepcopy
from typing import Any





# ---- 宿主 patch 兼容层（由 tools/inject_host_patch_shim.py 注入）----
# PyTest 里 patch("...daily_state._today_key") 期望改动能被本模块感知。
# 原 import 会被下面的同名函数覆盖，方法体调用时实时转发到宿主模块。
def _now_ts(*args, **kwargs):
    from . import daily_state as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)

class DailyStateContextSnapshotMixin:
    """上下文快照域（从 DailyStateMixin 拆出）。"""


    def _active_memo_notes(self, *, include_completed: bool = False) -> list[dict[str, Any]]:
        now = _now_ts()
        raw_notes = self.data.get("memo_notes") if isinstance(self.data.get("memo_notes"), list) else []
        notes = [note for note in (normalize_memo_note(item, now=now) for item in raw_notes) if note]
        if not include_completed:
            notes = [note for note in notes if note.get("status") == "active"]
        notes.sort(key=lambda item: memo_note_sort_key(item, now=now))
        return notes

    def _format_memo_notes_prompt_section(
        self,
        *,
        days: int = 3,
        include_pinned: bool = True,
        limit: int = 6,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="memo.active_notes",
                title="备忘便签",
                source="daily_state",
                content=content,
            )

        now = _now_ts()
        horizon = now + max(0, int(days)) * 86400
        rows: list[dict[str, Any]] = []
        for note in self._active_memo_notes():
            due_at = _safe_float(note.get("due_at"), 0)
            if not (include_pinned and note.get("pinned")) and not (due_at > 0 and due_at <= horizon):
                continue
            rows.append(note)
        if not rows:
            return build_section()
        lines: list[str] = []
        for note in rows[: max(1, int(limit or 1))]:
            due_at = _safe_float(note.get("due_at"), 0)
            due_text = self._environment_fromtimestamp(due_at).strftime("%m-%d %H:%M") if due_at > 0 else "未设时间"
            repeat = {
                "daily": "每天",
                "weekly": "每周",
                "monthly": "每月",
                "yearly": "每年",
            }.get(str(note.get("repeat") or "none"), "不重复")
            text = _single_line(note.get("title") or note.get("content"), 80)
            detail = _single_line(note.get("content"), 120)
            if detail and detail != text:
                text = f"{text}；{detail}"
            lines.append(f"- {due_text}｜{repeat}｜{text}")
        lines.append("这些是用户主动保存的待办/提醒，不是已经发生的经历；只在当前话题或时间相关时自然承接。")
        return build_section("\n".join(lines))

    def _format_memo_notes_for_prompt(
        self,
        *,
        days: int = 3,
        include_pinned: bool = True,
        limit: int = 6,
    ) -> str:
        """Render memo notes for the legacy background planning prompt."""

        section = self._format_memo_notes_prompt_section(
            days=days,
            include_pinned=include_pinned,
            limit=limit,
        )
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_memo_note_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        section = self._format_memo_note_prompt_section(user, reason=reason)
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_memo_note_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="memo.due_reminder",
                title="到期备忘便签",
                source="daily_state",
                content=content,
            )

        if reason != "memo_note_reminder" or not isinstance(user, dict):
            return build_section()
        context = user.get("planned_memo_note_context")
        if not isinstance(context, dict):
            return build_section()
        body = (
            f"- 标题：{_single_line(context.get('title'), 60) or '未命名便签'}\n"
            f"- 内容：{_single_line(context.get('content'), 240) or '无补充内容'}\n"
            f"- 到期：{_single_line(context.get('due_text'), 40) or '刚刚到期'}\n"
            "这是用户自己设置并已到期的提醒。直接自然提醒事项本身，不解释便签系统、调度或后台字段，不责怪用户，也不要虚构已完成。"
        )
        return build_section(body)

    def _next_memo_due_in_seconds(self, now: float | None = None) -> float | None:
        check_now = _safe_float(now, _now_ts())
        disabled_getter = getattr(self, "_proactive_generation_disabled", None)
        if callable(disabled_getter) and disabled_getter():
            return None
        waits: list[float] = []
        for note in self._active_memo_notes():
            due_at = _safe_float(note.get("due_at"), 0)
            if not note.get("remind_enabled") or due_at <= 0:
                continue
            if due_at > check_now:
                waits.append(due_at - check_now)
                continue
            last_offer = _safe_float(note.get("last_reminder_offer_at"), 0)
            last_attempt = _safe_float(note.get("last_reminder_attempt_at"), 0)
            if last_offer > 0:
                waits.append(max(0.0, last_offer + 24 * 3600 - check_now))
            elif last_attempt > 0:
                waits.append(max(0.0, last_attempt + 10 * 60 - check_now))
            else:
                waits.append(0.0)
        return min(waits) if waits else None

    async def _maybe_process_memo_notes(self, *, force: bool = False) -> None:
        now = _now_ts()
        async with self._data_lock:
            raw_notes = self.data.get("memo_notes")
            if not isinstance(raw_notes, list) or not raw_notes:
                return
            notes = [note for note in (normalize_memo_note(item, now=now) for item in raw_notes) if note]
            changed = len(notes) != len(raw_notes)
            for note in notes:
                if note.get("status") != "active" or not note.get("remind_enabled"):
                    continue
                due_at = _safe_float(note.get("due_at"), 0)
                if due_at <= 0 or due_at > now:
                    continue
                last_offer = _safe_float(note.get("last_reminder_offer_at"), 0)
                if last_offer > 0 and now - last_offer < 24 * 3600 and not force:
                    continue
                last_attempt = _safe_float(note.get("last_reminder_attempt_at"), 0)
                if last_offer <= 0 and last_attempt > 0 and now - last_attempt < 10 * 60 and not force:
                    continue
                title = _single_line(note.get("title") or note.get("content"), 60) or "一张便签"
                content = _single_line(note.get("content"), 240)
                due_text = self._environment_fromtimestamp(due_at).strftime("%Y-%m-%d %H:%M")
                context = {
                    "memo_id": _single_line(note.get("id"), 64),
                    "title": title,
                    "content": content,
                    "due_at": due_at,
                    "due_text": due_text,
                    "repeat": _single_line(note.get("repeat"), 20),
                    "due_state": memo_note_due_state(note, now=now),
                }
                offered = 0
                note["last_reminder_attempt_at"] = now
                changed = True
                for user_id, user in self._personal_goal_owner_users():
                    scheduled = now + random.uniform(8, 35)
                    candidate = {
                        "source": "memo_note",
                        "reason": "memo_note_reminder",
                        "action": "message",
                        "scheduled_ts": scheduled,
                        "window_start_at": scheduled,
                        "preferred_ts": scheduled,
                        "best_until_at": scheduled + 2 * 3600,
                        "expire_at": scheduled + 8 * 3600,
                        "topic": title,
                        "motive": f"用户保存的便签“{title}”已经到期，需要自然提醒一次",
                        "score": 96,
                        "context_key": "planned_memo_note_context",
                        "context": deepcopy(context),
                    }
                    if self._offer_proactive_candidate(user_id, user, candidate):
                        offered += 1
                if offered > 0:
                    note["last_reminder_offer_at"] = now
                    note["updated_at"] = max(_safe_float(note.get("updated_at"), 0), now)
                    changed = True
            if changed:
                self.data["memo_notes"] = notes[-200:]
                self._save_data_sync(
                    sections={"memo_notes", "users", "proactive_candidate_pool"}
                )

    def _format_memo_notes_injection(self) -> str:
        section = self._format_memo_notes_prompt_section(
            days=2,
            include_pinned=False,
            limit=4,
        )
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _user_asks_recent_bot_activity(self, text: str) -> bool:
        normalized = _single_line(text, 180)
        if not normalized:
            return False
        third_party_checker = getattr(self, "_user_activity_question_targets_someone_else", None)
        if callable(third_party_checker) and third_party_checker(normalized):
            return False
        direct_checker = getattr(self, "_user_asks_bot_current_state_or_activity", None)
        if callable(direct_checker) and direct_checker(normalized):
            return True
        return bool(
            re.search(
                r"(最近|刚才|现在|今天|这两天|这会儿).{0,12}(在)?(干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥|弄什么|写什么|写了什么|创作什么|创作了什么|玩什么|折腾什么)|"
                r"你.{0,8}(在)?(干嘛|干啥|干什么|做什么|做啥|忙什么|忙啥|写什么|写了什么|弄什么|创作什么|创作了什么)",
                normalized,
            )
            or re.search(
                r"你.{0,10}(吃饭|吃过|喝水|睡|休息|累不累|困不困|在不在|在哪|出门|上课|工作|学习|看书|画图|忙不忙)",
                normalized,
            )
        )

    def _user_asks_recent_creative_activity(self, text: str) -> bool:
        normalized = _single_line(text, 220)
        if not normalized:
            return False
        if self._user_asks_bookshelf_creative_inventory(normalized):
            return True
        if re.search(r"(最近|刚才|现在|今天|这两天|这会儿|近来).{0,18}(创作|作品|写作|草稿|手稿|写了什么|写什么|诗|小说|随笔|散文|剧本|设定|世界观|歌词)", normalized):
            return True
        if re.search(r"你.{0,10}(创作|作品|写作|草稿|手稿|写了什么|写什么|写诗|写小说|写随笔|写剧本|写设定)", normalized):
            return True
        if re.search(r"(有什么|写了啥|写了什么|能不能看看|给我看看).{0,14}(创作|作品|草稿|诗|小说|随笔|剧本|设定|片段)", normalized):
            return True
        if self._user_asks_creative_work_existence(normalized):
            return True
        return False

    @staticmethod
    def _user_asks_bookshelf_creative_inventory(text: str) -> bool:
        normalized = _single_line(text, 220)
        if not normalized or any(
            token in normalized for token in ("资料柜密码", "书架密码", "夹层密码", "抽屉密码")
        ):
            return False
        return bool(
            re.search(
                r"(?:资料柜|书架|作品柜|创作柜).{0,12}(?:能看到|看得到|能看见|可以看|看看|查一下|查询|检索|列一下|列出|有什么|有哪些|有几|多少|空不空|是不是空|还是空|空的)",
                normalized,
            )
            or re.search(
                r"(?:能看到|看得到|能看见|可以看|看看|查一下|查询|检索|列一下|列出).{0,12}(?:资料柜|书架|作品柜|创作柜)",
                normalized,
            )
        )

    @staticmethod
    def _user_asks_creative_work_existence(text: str) -> bool:
        normalized = _single_line(text, 220)
        if not normalized:
            return False
        work_terms = r"书|小说|故事|作品|诗|随笔|散文|剧本|手稿|草稿|设定集"
        author_actions = r"写过|写了|写完|写着|在写|写没写|有没有写|没写过|没写|会写|创作过|做过|出过|出版过"
        patterns = (
            rf"(?:你|自己|本人)[^。！？!?\n]{{0,12}}(?:{author_actions})[^。！？!?\n]{{0,10}}(?:{work_terms})",
            rf"(?:{author_actions})[^。！？!?\n]{{0,10}}(?:{work_terms})",
            rf"(?:有|有没有|没有|没)[^。！？!?\n]{{0,8}}(?:自己写的|自己创作的|自己的)[^。！？!?\n]{{0,5}}(?:{work_terms})",
            rf"(?:你|自己)[^。！？!?\n]{{0,8}}(?:的)?(?:{work_terms})[^。！？!?\n]{{0,8}}(?:呢|吗|嘛|在哪|叫什么|有几(?:本|篇|个))",
            rf"(?:那|这|哪|几)[^。！？!?\n]{{0,4}}(?:本书|本小说|篇作品)[^。！？!?\n]{{0,10}}(?:写到|写完|续写|后续|进度|作品内容|作品名字|作品标题)",
        )
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)

    def _mentioned_creative_project_title(self, text: str) -> str:
        normalized = _single_line(text, 260)
        if not normalized:
            return ""
        best = ""
        for project in self._creative_projects():
            if project.get("status") not in {"drafting", "finished"}:
                continue
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if not chunks:
                continue
            title = _single_line(project.get("title"), 60)
            if len(title) < 2:
                continue
            if title in normalized and len(title) > len(best):
                best = title
        return best

    @staticmethod
    def _creative_query_work_type_score(inbound_text: str, work_type: str, title: str = "") -> int:
        text = _single_line(inbound_text, 220)
        target = f"{work_type} {title}"
        score = 0
        groups = (
            (("诗", "短诗", "歌词", "歌"), ("诗", "歌词", "歌")),
            (("小说", "短篇"), ("小说", "短篇", "故事")),
            (("随笔", "散文", "札记"), ("随笔", "散文", "札记")),
            (("剧本", "短剧", "分镜", "对白", "脚本"), ("剧本", "短剧", "分镜", "对白", "脚本")),
            (("设定", "世界观", "角色", "怪谈", "图鉴"), ("设定", "世界观", "角色", "怪谈", "图鉴")),
        )
        for query_tokens, type_tokens in groups:
            if any(token in text for token in query_tokens) and any(token in target for token in type_tokens):
                score += 10
        if title and title in text:
            score += 20
        return score

    def _recent_creative_share_snapshot(
        self,
        user: dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        snapshot = user.get("last_creative_share_snapshot")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        expires_at = _safe_float(snapshot.get("expires_at"), 0)
        if expires_at <= 0 and sent_at > 0:
            expires_at = sent_at + 12 * 3600
        if sent_at <= 0 or expires_at <= check_now:
            return {}
        return snapshot

    def _remember_recent_creative_share_snapshot(
        self,
        user: dict[str, Any],
        *,
        creative_context: dict[str, Any] | None,
        shared_text: str,
        sent_at: float | None = None,
    ) -> None:
        if not isinstance(user, dict):
            return
        context = creative_context if isinstance(creative_context, dict) else {}
        delivered_text = self._visible_text_without_tts_reading(shared_text, limit=1600)
        source_snippet = _single_line(context.get("snippet"), 420)
        if not delivered_text and not source_snippet:
            return
        delivered_at = _now_ts() if sent_at is None else sent_at
        user["last_creative_share_snapshot"] = {
            "project_id": _single_line(context.get("project_id"), 32),
            "title": _single_line(context.get("title"), 60),
            "work_type": _single_line(context.get("work_type"), 30),
            "premise": _single_line(context.get("premise"), 180),
            "shared_text": _single_line(delivered_text, 1600),
            "source_snippet": source_snippet,
            "sent_at": delivered_at,
            "expires_at": delivered_at + 12 * 3600,
        }

    def _format_recent_creative_share_snapshot_for_reply_prompt_section(
        self,
        user: dict[str, Any] | None,
        inbound_text: str,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="creative.recent_share",
                title="最近一次真实创作分享",
                source="daily_state",
                content=content,
            )

        snapshot = self._recent_creative_share_snapshot(user)
        if not snapshot:
            return build_section()
        inbound = _single_line(inbound_text, 220)
        if not inbound:
            return build_section()
        work_title = _single_line(snapshot.get("title"), 60)
        title_mentioned = bool(work_title and work_title in inbound)
        asks_creative = self._user_asks_recent_creative_activity(inbound)
        asks_activity = self._user_asks_recent_bot_activity(inbound)
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        nearby_short_followup = sent_at > 0 and _now_ts() - sent_at <= 30 * 60 and len(inbound) <= 72
        if not (title_mentioned or asks_creative or asks_activity or nearby_short_followup):
            return build_section()
        direct_query = title_mentioned or asks_creative or asks_activity
        shared_text = _single_line(snapshot.get("shared_text"), 1600)
        source_snippet = _single_line(snapshot.get("source_snippet"), 420)
        shown_text = shared_text if direct_query else _single_line(shared_text, 360)
        body = "\n".join(
            part
            for part in (
                "这是刚刚已实际发送给当前用户的创作内容，不是待发送计划。只有本轮确实在承接这次分享时才使用；无关时完全忽略。",
                f"作品类型：{_single_line(snapshot.get('work_type'), 30)}" if snapshot.get("work_type") else "",
                f"标题：{work_title}" if work_title else "",
                f"设定：{_single_line(snapshot.get('premise'), 180)}" if snapshot.get("premise") else "",
                f"实际分享正文：{shown_text}" if shown_text else "",
                f"分享时对应片段：{source_snippet}" if source_snippet and source_snippet != shown_text else "",
                "用户若在追问内容、人物、设定或后续，先围绕这次实际分享回答；不要把后来推进的新片段冒充成刚才发出的内容。",
            )
            if part
        )
        return build_section(body)

    def _recent_photo_share_snapshot(
        self,
        user: dict[str, Any] | None,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        snapshot = user.get("last_photo_share_snapshot")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        expires_at = _safe_float(snapshot.get("expires_at"), 0) or sent_at + 12 * 3600
        if sent_at <= 0 or expires_at <= check_now:
            return {}
        return snapshot

    def _remember_recent_photo_share_snapshot(
        self,
        user: dict[str, Any],
        *,
        caption: str,
        topic: str = "",
        motive: str = "",
        reason: str = "",
        subject_owner: str = "",
        sent_at: float | None = None,
    ) -> None:
        if not isinstance(user, dict):
            return
        normalized_caption = _single_line(caption, 260)
        if not normalized_caption:
            return
        normalized_owner = _normalize_photo_subject_owner(subject_owner)
        if not normalized_owner:
            normalized_owner = (
                "bot"
                if re.search(r"(?:^|[，,。！？!?\s])我(?:正|在|刚|把|的|坐|站|走|拿|看|拍|穿|写|做)", normalized_caption)
                else "unknown"
            )
        delivered_at = _now_ts() if sent_at is None else sent_at
        user["last_photo_share_snapshot"] = {
            "schema_version": 2,
            "sender_owner": "bot",
            "subject_owner": normalized_owner,
            "caption": normalized_caption,
            "topic": _single_line(topic, 100),
            "motive": _single_line(motive, 180),
            "reason": _single_line(reason, 40),
            "sent_at": delivered_at,
            "expires_at": delivered_at + 12 * 3600,
        }

    def _format_recent_photo_share_snapshot_for_reply_prompt_section(
        self,
        user: dict[str, Any] | None,
        inbound_text: str,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="photo.recent_share",
                title="最近一次真实图片分享",
                source="daily_state",
                content=content,
            )

        snapshot = self._recent_photo_share_snapshot(user)
        if not snapshot:
            return build_section()
        inbound = _single_line(inbound_text, 220)
        if not inbound:
            return build_section()
        sent_at = _safe_float(snapshot.get("sent_at"), 0)
        recent_short_followup = sent_at > 0 and _now_ts() - sent_at <= 30 * 60 and len(inbound) <= 40
        asks_photo = any(
            token in inbound.lower()
            for token in (
                "图", "画", "照片", "图片", "刚才", "这是", "什么", "哪张", "哪里", "好看", "？", "?",
            )
        )
        if not (recent_short_followup or asks_photo):
            return build_section()
        subject_owner = _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown"
        owner_label = _photo_subject_owner_prompt_label(subject_owner)
        body = "\n".join(
            part
            for part in (
                "这是刚刚已经实际发送给当前用户的图片语义。若本轮是在追问该图，必须以这里为准；不要用旧梦境、旧日程或其他图片自行补写来源。",
                "图片发送者：Bot/当前人格",
                f"画面主体归属：{owner_label}",
                f"图片画面（主体={owner_label}）：{_single_line(snapshot.get('caption'), 260)}",
                f"分享话题：{_single_line(snapshot.get('topic'), 100)}" if snapshot.get("topic") else "",
                f"当时动机：{_single_line(snapshot.get('motive'), 180)}" if snapshot.get("motive") else "",
                "归属边界：用户的短句通常是在评价图中画面，不代表用户亲自做了图中的动作。严格服从上面的结构化主体归属，不要仅凭“她”猜主语；只有用户明确说“我做了/我弄洒了”时才归到用户。",
                "承接时不得把画面事故反过来责怪用户，也不要问用户是否被图里的事故溅到、弄伤或弄湿；应由 Bot/画面主体自然认领并回应。",
                "如果用户只发“？”或问这是什么，直接简短解释这张图；不要声称它来自未发生的梦境、课堂或现实经历。",
            )
            if part
        )
        return build_section(body)

    def _format_hidden_creative_context_for_reply_prompt_section(
        self,
        inbound_text: str,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="creative.hidden_context",
                title="私下创作近况",
                source="daily_state",
                content=content,
            )

        if not runtime_persona_setting(self, "enable_creative_writing", False):
            return build_section()
        recent_share_context = self._format_recent_creative_share_snapshot_for_reply_prompt_section(
            user,
            inbound_text,
        )
        if str(recent_share_context.content or "").strip():
            return recent_share_context
        mentioned_title = self._mentioned_creative_project_title(inbound_text)
        asks_creative = self._user_asks_recent_creative_activity(inbound_text)
        asks_existence = self._user_asks_creative_work_existence(inbound_text)
        asks_bookshelf_inventory = self._user_asks_bookshelf_creative_inventory(inbound_text)
        asks_activity = self._user_asks_recent_bot_activity(inbound_text)
        if not (mentioned_title or asks_creative or asks_activity):
            return build_section()
        available_projects: list[dict[str, Any]] = []
        for project in self._creative_projects():
            if project.get("status") not in {"drafting", "finished"}:
                continue
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            if chunks:
                available_projects.append(project)
        candidates = []
        for project in reversed(available_projects):
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            latest = next((item for item in reversed(chunks) if isinstance(item, dict) and _single_line(item.get("text"), 180)), None)
            score = self._creative_query_work_type_score(
                inbound_text,
                self._creative_work_type(project),
                _single_line(project.get("title"), 40),
            )
            if mentioned_title and mentioned_title == _single_line(project.get("title"), 60):
                score += 100
            candidates.append((score, project, latest))
            if len(candidates) >= 4 and (not mentioned_title or any(item[0] >= 100 for item in candidates)):
                break
        if not candidates:
            if asks_bookshelf_inventory:
                title = "资料柜创作区真实库存"
                body = (
                    "用户正在询问能否看到资料柜或资料柜里有什么。当前资料柜创作区确实没有保存过正文的作品。\n"
                    "必须直接说明真实结果；不要假装翻找，不要用括号动作、挠头或含糊的场景描写代替回答。"
                )
                return prompt_section(
                    key="creative.inventory_empty",
                    title=title,
                    source="daily_state",
                    content=body,
                )
            return build_section()
        if mentioned_title or asks_creative:
            candidates.sort(key=lambda item: item[0], reverse=True)
        _, project, latest = candidates[0]
        work_type = self._creative_work_type(project)
        work_title = _single_line(project.get("title"), 40)
        premise = _single_line(project.get("premise"), 120)
        progress = f"{_safe_int(project.get('current_chars'), 0, 0)}/{_safe_int(project.get('target_chars'), 2400, 300, 5200)}"
        snippet = _single_line((latest or {}).get("text"), 180) if isinstance(latest, dict) else ""
        ask_line = (
            f"用户提到了你私下创作过的作品《{mentioned_title}》。"
            if mentioned_title
            else
            "用户正在询问能否看到资料柜或资料柜里有哪些真实内容。"
            if asks_bookshelf_inventory
            else
            "用户正在确认你是否写过自己的书、小说或其他文本作品。"
            if asks_existence
            else
            "用户正在明确询问你最近的创作、写作、草稿或某类作品。"
            if asks_creative
            else "用户正在询问你最近在做什么。"
        )
        creative_continuity_hint = '如果前文刚提到上述标题或片段，用“我之前写过/发你看过”这类说法会更自然；不必主动解释作者归属。'
        finished_count = sum(1 for item in available_projects if item.get("status") == "finished")
        drafting_count = sum(1 for item in available_projects if item.get("status") == "drafting")
        recent_titles = [
            _single_line(item.get("title"), 50)
            for item in reversed(available_projects)
            if _single_line(item.get("title"), 50)
        ][:3]
        inventory_hint = (
            f"真实创作记录：共有 {len(available_projects)} 个已有正文的文本作品，其中已完成 {finished_count} 个、仍在写 {drafting_count} 个。"
            + (f"近期标题示例：{'、'.join(recent_titles)}。" if recent_titles else "")
        )
        existence_rule = (
            "用户问的是是否写过作品：必须明确承认这些真实创作记录，不能回答“没写过书/没有自己的作品”。"
            "这些记录证明写过文本作品，但不等于正式出版或发行过实体书；若用户明确问出版，只能如实区分。"
            if asks_existence
            else ""
        )
        body = (
            f"{ask_line}你可以提到：你最近因为生活小事、日记碎片或梦境灵感开了一个自己的文本作品,一直在自己慢慢写。\n"
            f"{inventory_hint}\n"
            f"作品类型：{work_type}\n"
            f"标题：{work_title or '未定标题'}\n"
            f"设定：{premise or '还没完全想清楚'}\n"
            f"进度：约 {progress} 字\n"
            + f"\n{creative_continuity_hint}\n"
            + (f"{existence_rule}\n" if existence_rule else "")
            + (f"最近一句/片段：{snippet}\n" if snippet else "")
            + "如果用户询问资料柜库存，必须直接依据真实数量和标题回答，禁止用括号动作或假装翻找代替结果。如果用户明确问指定作品的正文、某一部分、写作想法或作者怎么看，下面的短片段只能用于定位，必须先调用 pc_view_creative_work 读取真实正文后再回答；不要先发“我去看看”，也不要凭短片段假装已经读完。若只是泛问最近有没有创作，可以直接概括并给一小句片段。否则这不是必须回答的内容，可以只含糊说“在弄一点小东西”。不要主动汇报系统进度，不要一次给完整正文。"
        )
        return build_section(body)
